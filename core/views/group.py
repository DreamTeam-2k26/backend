from django.db.models import Count
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from core.models import Group, User
from core.permissions import IsSuperuser, ModelPermissions
from core.serializers import GroupSerializer, GroupUsersSerializer, UserSerializer


class GroupViewSet(ModelViewSet):
    """CRUD dos grupos (tipos de usuário) e gestão de seus membros."""

    queryset = Group.objects.all().order_by('name')
    serializer_class = GroupSerializer
    permission_classes = [IsAuthenticated, ModelPermissions]
    # GET /api/groups/{id}/users/ devolve dados de usuários, então exige as duas permissões.
    action_permissions = {'users': ['core.view_group', 'core.view_user']}
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['is_default']
    search_fields = ['name', 'slug', 'description']
    ordering_fields = ['name', 'created_at']

    def get_queryset(self):
        """Anota a quantidade de usuários de cada grupo."""
        # O nome de query da relação reversa é 'user' (o accessor é 'users').
        return super().get_queryset().annotate(users_count=Count('user'))

    def _group_response(self, group, http_status=status.HTTP_200_OK):
        """Serializa o grupo recarregando a anotação de membros."""
        group = self.get_queryset().get(pk=group.pk)

        return Response(self.get_serializer(group).data, status=http_status)

    @extend_schema(
        summary='Usuários do grupo',
        description='Retorna os usuários que pertencem ao grupo.',
        responses={200: UserSerializer(many=True)},
    )
    @action(detail=True, methods=['get'])
    def users(self, request, pk=None):
        """Lista os usuários do grupo."""
        group = self.get_object()
        queryset = group.users.all().order_by('id')
        page = self.paginate_queryset(queryset)

        if page is not None:
            serializer = UserSerializer(page, many=True, context=self.get_serializer_context())
            return self.get_paginated_response(serializer.data)

        serializer = UserSerializer(queryset, many=True, context=self.get_serializer_context())

        return Response(serializer.data)

    @extend_schema(
        summary='Adicionar usuários ao grupo',
        description='Adiciona um ou mais usuários ao grupo. Restrito a superusuários.',
        request=GroupUsersSerializer,
        responses={200: GroupSerializer},
    )
    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated, IsSuperuser])
    def add_users(self, request, pk=None):
        """Adiciona usuários ao grupo."""
        group = self.get_object()
        serializer = GroupUsersSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        users = User.objects.filter(pk__in=serializer.validated_data['users'])
        group.users.add(*users)

        return self._group_response(group)

    @extend_schema(
        summary='Remover usuários do grupo',
        description='Remove um ou mais usuários do grupo. Restrito a superusuários.',
        request=GroupUsersSerializer,
        responses={200: GroupSerializer},
    )
    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated, IsSuperuser])
    def remove_users(self, request, pk=None):
        """Remove usuários do grupo."""
        group = self.get_object()
        serializer = GroupUsersSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        users = User.objects.filter(pk__in=serializer.validated_data['users'])
        group.users.remove(*users)

        return self._group_response(group)
