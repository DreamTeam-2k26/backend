from django.db.models import OuterRef, Subquery
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from core.models import ChecklistItem, Membership
from core.permissions import ChecklistItemPermission
from core.serializers import (
    ChecklistItemCreateUpdateSerializer,
    ChecklistItemListSerializer,
    ChecklistItemReorderSerializer,
    ChecklistItemRetrieveSerializer,
)


@extend_schema_view(
    list=extend_schema(summary='Itens de checklist, por posição (filtre com ?task=<id>)'),
    create=extend_schema(summary='Criar item (qualquer participante; sem posição entra no fim)'),
    retrieve=extend_schema(summary='Detalhes do item'),
    update=extend_schema(summary='Editar item (qualquer participante)'),
    partial_update=extend_schema(summary='Renomear ou marcar/desmarcar item (is_done)'),
    destroy=extend_schema(summary='Excluir item (qualquer participante)'),
)
class ChecklistItemViewSet(ModelViewSet):
    """Checklist das tarefas dos projetos do usuário autenticado.

    Qualquer participante do projeto da tarefa opera os itens (ChecklistItemPermission).
    Item de tarefa de projeto alheio responde 404.
    """

    queryset = ChecklistItem.objects.none()  # só para o schema/filtros descobrirem o model; o real é get_queryset
    permission_classes = [IsAuthenticated, ChecklistItemPermission]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ('task', 'is_done')
    lookup_value_regex = r'\d+'

    def get_queryset(self):
        user = self.request.user
        my_role = Membership.objects.filter(project=OuterRef('task__project'), user=user).values('role')[:1]
        return (
            ChecklistItem.objects
            .filter(task__project__memberships__user=user)
            .annotate(my_role=Subquery(my_role))
            .order_by('task_id', 'position', 'id')
        )

    def get_serializer_class(self):
        if self.action == 'list':
            return ChecklistItemListSerializer
        if self.action in {'create', 'update', 'partial_update'}:
            return ChecklistItemCreateUpdateSerializer
        return ChecklistItemRetrieveSerializer

    @extend_schema(
        summary='Reordenar todos os itens do checklist de uma tarefa (qualquer participante)',
        request=ChecklistItemReorderSerializer,
        responses={200: ChecklistItemListSerializer(many=True)},
    )
    @action(detail=False, methods=['post'])
    def reorder(self, request):
        serializer = ChecklistItemReorderSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        items = serializer.save()
        return Response(ChecklistItemListSerializer(items, many=True).data, status=status.HTTP_200_OK)
