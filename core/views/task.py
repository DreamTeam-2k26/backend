from django.db.models import Case, IntegerField, OuterRef, Q, Subquery, Value, When
from django.utils import timezone
from django_filters.rest_framework import BooleanFilter, ChoiceFilter, DjangoFilterBackend, FilterSet
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from core.models import Column, Membership, Task
from core.permissions import TaskPermission
from core.serializers import (
    TaskCreateUpdateSerializer,
    TaskListSerializer,
    TaskMoveSerializer,
    TaskRetrieveSerializer,
)


class TaskFilter(FilterSet):
    """Filtros do quadro e do dashboard. status e overdue usam a categoria interna da coluna."""

    status = ChoiceFilter(field_name='column__category', choices=Column.Category.choices, label='Categoria da coluna')
    overdue = BooleanFilter(
        method='filter_overdue', label='Atrasadas: prazo anterior a hoje e fora de colunas de concluídas'
    )

    class Meta:
        model = Task
        fields = {
            'project': ['exact'],
            'column': ['exact'],
            'assignee': ['exact', 'isnull'],
            'priority': ['exact'],
            'due_date': ['exact', 'gte', 'lte', 'isnull'],
        }

    def filter_overdue(self, queryset, name, value):
        late = Q(due_date__lt=timezone.localdate()) & ~Q(column__category=Column.Category.DONE)
        return queryset.filter(late) if value else queryset.exclude(late)


@extend_schema_view(
    list=extend_schema(
        summary='Tarefas dos projetos do usuário (filtros, ?search= no título/descrição e ?ordering=)',
        description='Sem ?ordering, segue a ordem do quadro (coluna e posição). Para prioridade use '
        'ordering=priority_rank (baixa → alta) ou -priority_rank.',
    ),
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
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = TaskFilter
    search_fields = ('title', 'description')
    ordering_fields = ('due_date', 'created_at', 'updated_at', 'title', 'position', 'priority_rank')
    ordering = ('project_id', 'column__position', 'position', 'id')
    lookup_value_regex = r'\d+'

    def get_queryset(self):
        user = self.request.user
        my_projects = Membership.objects.filter(user=user).values('project')
        my_role = Membership.objects.filter(project=OuterRef('project'), user=user).values('role')[:1]
        return (
            Task.objects
            .filter(project__in=my_projects)
            .annotate(
                my_role=Subquery(my_role),
                # Ordenar por "priority" seria alfabético (high, low, medium); priority_rank segue a gravidade.
                priority_rank=Case(
                    When(priority=Task.Priority.LOW, then=Value(0)),
                    When(priority=Task.Priority.MEDIUM, then=Value(1)),
                    When(priority=Task.Priority.HIGH, then=Value(2)),
                    output_field=IntegerField(),
                ),
            )
            .with_checklist_counts()
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

    @extend_schema(
        summary='Mover/reordenar tarefa (drag-and-drop; qualquer participante)',
        description='Leva a tarefa para column_id na posição pedida (posição além do fim vai para o fim). '
        'Responde com as tarefas das colunas afetadas, já na ordem nova.',
        request=TaskMoveSerializer,
        responses={200: TaskListSerializer(many=True)},
    )
    @action(detail=True, methods=['post'])
    def move(self, request, pk=None):
        serializer = TaskMoveSerializer(
            data=request.data, context={**self.get_serializer_context(), 'task': self.get_object()}
        )
        serializer.is_valid(raise_exception=True)
        task = serializer.save()
        return Response(TaskListSerializer(serializer.board(task), many=True).data, status=status.HTTP_200_OK)
