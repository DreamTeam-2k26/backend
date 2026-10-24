from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from chat import actions, services
from chat.serializers import MessageCreateSerializer, MessageSerializer, RoomDetailSerializer, RoomSerializer

from ._common import domain_errors


class RoomViewSet(viewsets.GenericViewSet):
    """Salas de conversa dos projetos do usuário autenticado.

    As salas não são criadas nem administradas aqui: cada projeto tem a sua, e nome e participantes seguem o
    projeto (chat/project_rooms.py). Por isso não há criar, editar, sair nem gerenciar participantes.

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

    # ---- salas ----

    @extend_schema(
        summary='Salas do usuário',
        parameters=[OpenApiParameter('project', int, description='Só a sala deste projeto.')],
        responses={200: RoomSerializer(many=True)},
    )
    def list(self, request):
        project = request.query_params.get('project')

        if project not in (None, ''):
            try:
                project = int(project)
            except ValueError as error:
                raise ValidationError({'project': 'Informe um id numérico.'}) from error
        else:
            project = None

        page = self.paginate_queryset(services.rooms_for(request.user, project_id=project))
        serializer = RoomSerializer(page, many=True, context=self.get_serializer_context())

        return self.get_paginated_response(serializer.data)

    @extend_schema(summary='Detalhes da sala', responses={200: RoomDetailSerializer})
    def retrieve(self, request, pk=None):
        return self._detail(self._room(pk))

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
