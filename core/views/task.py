from django.db.models import OuterRef, Subquery
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from core.models import Membership, Task
from core.permissions import TaskPermission
from core.serializers import TaskCreateUpdateSerializer, TaskListSerializer, TaskRetrieveSerializer


@extend_schema_view(
    list=extend_schema(summary='Tarefas dos projetos do usuário, por coluna e posição (filtre com ?project=<id>)'),
    create=extend_schema(summary='Criar tarefa (qualquer participante; sem posição entra no fim da coluna)'),
    retrieve=extend_schema(summary='Detalhes da tarefa'),
    update=extend_schema(summary='Editar tarefa (qualquer participante)'),
    partial_update=extend_schema(summary='Editar/mover tarefa (qualquer participante)'),
    destroy=extend_schema(summary='Excluir tarefa (qualquer participante)'),
)
class TaskViewSet(ModelViewSet):
    """Tarefas do Kanban dos projetos do usuário autenticado.

    Qualquer participante cria, edita, move e exclui qualquer tarefa do projeto (TaskPermission).
    Tarefa de projeto alheio responde 404.
    """

    queryset = Task.objects.none()  # só para o schema/filtros descobrirem o model; o real é get_queryset
    permission_classes = [IsAuthenticated, TaskPermission]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ('project', 'column', 'assignee', 'priority')
    lookup_value_regex = r'\d+'

    def get_queryset(self):
        user = self.request.user
        my_projects = Membership.objects.filter(user=user).values('project')
        my_role = Membership.objects.filter(project=OuterRef('project'), user=user).values('role')[:1]
        return (
            Task.objects
            .filter(project__in=my_projects)
            .annotate(my_role=Subquery(my_role))
            .select_related('column', 'assignee', 'created_by')
            .order_by('project_id', 'column__position', 'position', 'id')
        )

    def get_serializer_class(self):
        if self.action == 'list':
            return TaskListSerializer
        if self.action in {'create', 'update', 'partial_update'}:
            return TaskCreateUpdateSerializer
        return TaskRetrieveSerializer

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)
