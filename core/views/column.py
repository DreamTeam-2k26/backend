from django.db.models import OuterRef, ProtectedError, Subquery
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from core.models import Column, Membership
from core.permissions import ColumnPermission
from core.serializers import (
    ColumnCreateUpdateSerializer,
    ColumnListSerializer,
    ColumnReorderSerializer,
    ColumnRetrieveSerializer,
)


@extend_schema_view(
    list=extend_schema(summary='Colunas dos projetos do usuário, por posição (filtre com ?project=<id>)'),
    create=extend_schema(summary='Criar coluna (dono ou administrador; entra no fim do quadro)'),
    retrieve=extend_schema(summary='Detalhes da coluna'),
    update=extend_schema(summary='Renomear/reposicionar coluna (dono ou administrador)'),
    partial_update=extend_schema(summary='Renomear/reposicionar coluna (dono ou administrador)'),
    destroy=extend_schema(summary='Excluir coluna sem tarefas (dono ou administrador)'),
)
class ColumnViewSet(ModelViewSet):
    """Colunas do Kanban dos projetos do usuário autenticado.

    Qualquer participante lê; só dono/admin alteram (ColumnPermission e validação do projeto no serializer).
    Coluna de projeto alheio responde 404.
    """

    queryset = Column.objects.none()  # só para o schema/filtros descobrirem o model; o real é get_queryset
    permission_classes = [IsAuthenticated, ColumnPermission]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ('project', 'category')
    lookup_value_regex = r'\d+'

    def get_queryset(self):
        user = self.request.user
        my_projects = Membership.objects.filter(user=user).values('project')
        my_role = Membership.objects.filter(project=OuterRef('project'), user=user).values('role')[:1]
        return (
            Column.objects
            .filter(project__in=my_projects)
            .annotate(my_role=Subquery(my_role))
            .order_by('project_id', 'position', 'id')
        )

    def get_serializer_class(self):
        if self.action == 'list':
            return ColumnListSerializer
        if self.action in {'create', 'update', 'partial_update'}:
            return ColumnCreateUpdateSerializer
        return ColumnRetrieveSerializer

    def perform_destroy(self, instance):
        same_category = instance.project.columns.filter(category=instance.category).exclude(pk=instance.pk)
        if not same_category.exists():
            raise ValidationError({
                'detail': 'O projeto precisa de pelo menos uma coluna de cada categoria (a fazer, em andamento, '
                'concluído).'
            })
        try:
            instance.delete()
        except ProtectedError as error:
            raise ValidationError({
                'detail': 'A coluna tem tarefas. Mova as tarefas para outra coluna antes de excluí-la.'
            }) from error

    @extend_schema(
        summary='Reordenar todas as colunas do projeto (dono ou administrador)',
        request=ColumnReorderSerializer,
        responses={200: ColumnListSerializer(many=True)},
    )
    @action(detail=False, methods=['post'])
    def reorder(self, request):
        serializer = ColumnReorderSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        columns = serializer.save()
        return Response(ColumnListSerializer(columns, many=True).data, status=status.HTTP_200_OK)
