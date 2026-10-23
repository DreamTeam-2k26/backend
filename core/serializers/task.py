from django.db.models import Max
from django.utils import timezone
from rest_framework.exceptions import NotFound
from rest_framework.serializers import (
    BooleanField,
    CharField,
    ModelSerializer,
    PrimaryKeyRelatedField,
    ValidationError,
)

from core.models import Membership, Task, User


def end_of_column(column, exclude=None):
    """Próxima posição livre no fim da coluna."""
    tasks = column.tasks.exclude(pk=exclude.pk) if exclude else column.tasks.all()
    last = tasks.aggregate(last=Max('position'))['last']
    return 0 if last is None else last + 1


class TaskListSerializer(ModelSerializer):
    assignee_name = CharField(source='assignee.name', read_only=True, default=None)
    is_overdue = BooleanField(read_only=True)

    class Meta:
        model = Task
        fields = (
            'id',
            'project',
            'column',
            'title',
            'priority',
            'assignee',
            'assignee_name',
            'due_date',
            'position',
            'completed_at',
            'is_overdue',
        )


class TaskRetrieveSerializer(ModelSerializer):
    column_name = CharField(source='column.name', read_only=True)
    assignee_name = CharField(source='assignee.name', read_only=True, default=None)
    created_by_name = CharField(source='created_by.name', read_only=True, default=None)
    is_overdue = BooleanField(read_only=True)

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
            'due_date',
            'position',
            'completed_at',
            'is_overdue',
            'created_by',
            'created_by_name',
            'created_at',
            'updated_at',
        )


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
