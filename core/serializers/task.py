from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from drf_spectacular.utils import extend_schema_field
from rest_framework.exceptions import NotFound
from rest_framework.serializers import (
    BooleanField,
    CharField,
    IntegerField,
    ModelSerializer,
    PrimaryKeyRelatedField,
    Serializer,
    SerializerMethodField,
    ValidationError,
)

from core.models import Column, Membership, Task, User
from core.serializers.file import FileListSerializer
from core.serializers.task_file import attached_files
from core.serializers.user import profile_picture_url


def end_of_column(column, exclude=None):
    """Próxima posição livre no fim da coluna."""
    tasks = column.tasks.exclude(pk=exclude.pk) if exclude else column.tasks.all()
    last = tasks.aggregate(last=Max('position'))['last']
    return 0 if last is None else last + 1


class TaskListSerializer(ModelSerializer):
    assignee_name = CharField(source='assignee.name', read_only=True, default=None)
    assignee_profile_picture = SerializerMethodField()
    is_overdue = BooleanField(read_only=True)
    checklist_total = IntegerField(read_only=True)  # anotado por Task.objects.with_checklist_counts()
    checklist_done = IntegerField(read_only=True)
    comments_count = IntegerField(read_only=True)  # anotado por Task.objects.with_comment_count()

    class Meta:
        model = Task
        fields = (
            'id',
            'project',
            'column',
            'title',
            'description',
            'priority',
            'assignee',
            'assignee_name',
            'assignee_profile_picture',
            'due_date',
            'position',
            'completed_at',
            'is_overdue',
            'checklist_total',
            'checklist_done',
            'comments_count',
        )

    def get_assignee_profile_picture(self, task) -> str | None:
        return profile_picture_url(task.assignee)


class TaskRetrieveSerializer(ModelSerializer):
    column_name = CharField(source='column.name', read_only=True)
    assignee_name = CharField(source='assignee.name', read_only=True, default=None)
    created_by_name = CharField(source='created_by.name', read_only=True, default=None)
    assignee_profile_picture = SerializerMethodField()
    created_by_profile_picture = SerializerMethodField()
    is_overdue = BooleanField(read_only=True)
    checklist_total = IntegerField(read_only=True)  # anotado por Task.objects.with_checklist_counts()
    checklist_done = IntegerField(read_only=True)
    comments_count = IntegerField(read_only=True)  # anotado por Task.objects.with_comment_count()
    files = SerializerMethodField(help_text='Arquivos anexados (gerencie em /api/tasks/{id}/files/).')

    class Meta:
        model = Task
        fields = (
            'id',
            'project',
            'column',
            'column_name',
            'title',
            'description',
            'priority',
            'assignee',
            'assignee_name',
            'assignee_profile_picture',
            'due_date',
            'position',
            'completed_at',
            'is_overdue',
            'checklist_total',
            'checklist_done',
            'comments_count',
            'files',
            'created_by',
            'created_by_name',
            'created_by_profile_picture',
            'created_at',
            'updated_at',
        )

    def get_assignee_profile_picture(self, task) -> str | None:
        return profile_picture_url(task.assignee)

    def get_created_by_profile_picture(self, task) -> str | None:
        return profile_picture_url(task.created_by)

    @extend_schema_field(FileListSerializer(many=True))
    def get_files(self, task):
        return FileListSerializer(attached_files(task), many=True).data


class TaskCreateUpdateSerializer(ModelSerializer):
    """Cria/edita tarefa. created_by vem do perform_create; completed_at segue a categoria da coluna."""

    assignee = PrimaryKeyRelatedField(queryset=User.objects.filter(is_active=True), required=False, allow_null=True)

    class Meta:
        model = Task
        fields = (
            'id',
            'project',
            'column',
            'title',
            'description',
            'priority',
            'assignee',
            'due_date',
            'position',
            'completed_at',
        )
        read_only_fields = ('completed_at',)
        extra_kwargs = {'position': {'required': False}}

    def validate_project(self, project):
        if self.instance and project != self.instance.project:
            raise ValidationError('Não é possível mover a tarefa para outro projeto.')
        if not Membership.objects.filter(project=project, user=self.context['request'].user).exists():
            raise NotFound('Projeto não encontrado.')
        return project

    def validate_due_date(self, due_date):
        changed = self.instance is None or due_date != self.instance.due_date
        if due_date is not None and changed and due_date < timezone.localdate():
            raise ValidationError('O prazo não pode ser anterior a hoje.')
        return due_date

    def validate(self, attrs):
        project = attrs.get('project') or self.instance.project
        column = attrs.get('column') or self.instance.column
        if column.project_id != project.pk:
            raise ValidationError({'column': 'A coluna precisa ser do mesmo projeto da tarefa.'})
        assignee = attrs.get('assignee')
        if assignee is not None and not Membership.objects.filter(project=project, user=assignee).exists():
            raise ValidationError({'assignee': 'O responsável precisa participar do projeto.'})
        return attrs

    def create(self, validated_data):
        if 'position' not in validated_data:
            validated_data['position'] = end_of_column(validated_data['column'])
        return super().create(validated_data)

    def update(self, task, validated_data):
        column = validated_data.get('column')
        if column is not None and column != task.column and 'position' not in validated_data:
            validated_data['position'] = end_of_column(column, exclude=task)
        return super().update(task, validated_data)


class TaskMoveSerializer(Serializer):
    """Drag-and-drop: leva a tarefa (context['task']) para column_id na posição pedida.

    Renumera 0, 1, 2... a coluna de destino e, se mudou de coluna, a de origem (fecha o buraco).
    completed_at é ajustado pelo Task.save conforme a categoria da coluna de destino.
    """

    column_id = IntegerField()
    position = IntegerField(min_value=0)

    def validate_column_id(self, column_id):
        column = Column.objects.filter(pk=column_id, project=self.context['task'].project).first()
        if column is None:
            raise ValidationError('A coluna precisa ser do mesmo projeto da tarefa.')
        self.column = column
        return column_id

    @transaction.atomic
    def create(self, validated_data):
        task = Task.objects.select_for_update().select_related('column').get(pk=self.context['task'].pk)
        column_ids = {task.column_id, self.column.pk}
        # Trava as tarefas das colunas envolvidas: dois arrastos simultâneos não geram posições repetidas.
        locked = Task.objects.select_for_update().filter(column_id__in=column_ids).order_by('position', 'id')
        by_column = {pk: [] for pk in column_ids}
        for item in locked:
            if item.pk != task.pk:
                by_column[item.column_id].append(item)

        target = by_column[self.column.pk]
        target.insert(min(validated_data['position'], len(target)), task)
        task.column = self.column

        changed = []
        for tasks in by_column.values():
            for position, item in enumerate(tasks):
                if item.position != position:
                    item.position = position
                    if item is not task:
                        changed.append(item)
        Task.objects.bulk_update(changed, ['position'])
        task.save()
        return task

    def board(self, task):
        """Tarefas das colunas afetadas, na ordem nova, para o frontend redesenhar o quadro."""
        column_ids = {self.context['task'].column_id, task.column_id}
        return (
            Task.objects
            .filter(column_id__in=column_ids)
            .with_checklist_counts()
            .with_comment_count()
            .select_related('column', 'assignee__profile_picture')
            .order_by('column__position', 'position', 'id')
        )
