from django.contrib.auth import get_user_model
from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from chat import actions, services
from chat.serializers import (
    MessageCreateSerializer,
    MessageSerializer,
    RoomAdminSerializer,
    RoomCreateSerializer,
    RoomDetailSerializer,
    RoomMembersSerializer,
    RoomSerializer,
    RoomUpdateSerializer,
)

from ._common import domain_errors


class RoomViewSet(viewsets.GenericViewSet):
    """Salas coletivas do usuário autenticado.

    A autorização aqui é por PARTICIPAÇÃO na sala (e administração, quando exigida), não pelas permissões
    de modelo dos grupos do projeto; por isso substitui o ModelPermissions padrão por IsAuthenticated.
    Uma sala da qual o usuário não participa responde 404, igual a uma sala inexistente.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = RoomSerializer
    lookup_value_regex = r'\d+'

    # ---- helpers ----

    def _room(self, pk):
        room = services.get_room_for(self.request.user, pk)

        if room is None:
            raise NotFound('Sala não encontrada.')

        return room

    def _detail(self, room, http_status=status.HTTP_200_OK):
        summary = services.room_summary(self.request.user, room)

        return Response(RoomDetailSerializer(summary, context=self.get_serializer_context()).data, status=http_status)

    def _target_user(self, user_id):
        user = get_user_model().objects.filter(pk=user_id, is_active=True).first()

        if user is None:
            raise NotFound('Usuário não encontrado.')

        return user

    # ---- salas ----

    @extend_schema(summary='Salas do usuário', responses={200: RoomSerializer(many=True)})
    def list(self, request):
        page = self.paginate_queryset(services.rooms_for(request.user))
        serializer = RoomSerializer(page, many=True, context=self.get_serializer_context())

        return self.get_paginated_response(serializer.data)

    @extend_schema(summary='Criar sala', request=RoomCreateSerializer, responses={201: RoomDetailSerializer})
    def create(self, request):
        serializer = RoomCreateSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        with domain_errors():
            room = actions.create_room(request.user, data['name'], data['members'], image=data.get('image'))

        return self._detail(room, status.HTTP_201_CREATED)

    @extend_schema(summary='Detalhes da sala', responses={200: RoomDetailSerializer})
    def retrieve(self, request, pk=None):
        return self._detail(self._room(pk))

    @extend_schema(summary='Editar sala (administradores)', request=RoomUpdateSerializer)
    def partial_update(self, request, pk=None):
        room = self._room(pk)
        serializer = RoomUpdateSerializer(data=request.data, partial=True, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        with domain_errors():
            actions.update_room(
                request.user,
                room,
                name=data.get('name'),
                image=data.get('image'),
                clear_image='image' in data and data['image'] is None,
            )

        return self._detail(room)

    @extend_schema(summary='Sair da sala', request=None, responses={204: None})
    @action(detail=True, methods=['post'])
    def leave(self, request, pk=None):
        room = self._room(pk)

        with domain_errors():
            actions.leave_room(request.user, room)

        return Response(status=status.HTTP_204_NO_CONTENT)

    # ---- participantes ----

    @extend_schema(summary='Adicionar participantes (administradores)', request=RoomMembersSerializer)
    @action(detail=True, methods=['post'], url_path='members')
    def add_members(self, request, pk=None):
        room = self._room(pk)
        serializer = RoomMembersSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)

        with domain_errors():
            actions.add_members(request.user, room, serializer.validated_data['users'])

        return self._detail(room)

    @extend_schema(summary='Remover participante (administradores)', request=None)
    @action(detail=True, methods=['delete'], url_path=r'members/(?P<user_id>\d+)', url_name='member')
    def remove_member(self, request, pk=None, user_id=None):
        room = self._room(pk)

        with domain_errors():
            actions.remove_member(request.user, room, self._target_user(user_id))

        return self._detail(room)

    @extend_schema(summary='Promover/rebaixar administrador', request=RoomAdminSerializer)
    @action(detail=True, methods=['post'], url_path=r'members/(?P<user_id>\d+)/admin', url_name='member-admin')
    def member_admin(self, request, pk=None, user_id=None):
        room = self._room(pk)
        serializer = RoomAdminSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        with domain_errors():
            actions.set_admin(request.user, room, self._target_user(user_id), serializer.validated_data['is_admin'])

        return self._detail(room)

    # ---- mensagens ----

    @extend_schema(
        summary='Histórico (GET) ou envio (POST) de mensagens',
        request=MessageCreateSerializer,
        responses={200: MessageSerializer(many=True), 201: MessageSerializer},
    )
    @action(detail=True, methods=['get', 'post'], url_path='messages')
    def messages(self, request, pk=None):
        room = self._room(pk)

        if request.method == 'POST':
            return self._create_message(request, room)

        before = request.query_params.get('before')

        if before not in (None, ''):
            try:
                before = int(before)
            except ValueError as error:
                raise ValidationError({'before': 'Informe um id numérico.'}) from error
        else:
            before = None

        page, has_more = services.message_page(room, before)
        read_ids = services.read_by_all_ids(room, page)
        data = MessageSerializer(page, many=True, context={'read_ids': read_ids}).data

        return Response({
            'results': data,
            'has_more': has_more,
            # Mensagens vêm em ordem cronológica: a mais antiga da página é o cursor da próxima.
            'next_before': page[0].id if has_more and page else None,
        })

    def _create_message(self, request, room):
        serializer = MessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        with domain_errors():
            message = actions.send_message(
                request.user, room, body=data['body'], file=data['file'], reply_to_id=data.get('reply_to')
            )

        read_ids = services.read_by_all_ids(room, [message])

        return Response(MessageSerializer(message, context={'read_ids': read_ids}).data, status=status.HTTP_201_CREATED)

    @extend_schema(summary='Marcar sala como lida', request=None, responses={204: None})
    @action(detail=True, methods=['post'])
    def read(self, request, pk=None):
        room = self._room(pk)

        with domain_errors():
            actions.mark_read(request.user, room)

        return Response(status=status.HTTP_204_NO_CONTENT)
