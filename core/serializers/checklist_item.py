from django.db import transaction
from django.db.models import Max
from rest_framework.exceptions import NotFound
from rest_framework.serializers import (
    IntegerField,
    ListField,
    ModelSerializer,
    PrimaryKeyRelatedField,
    Serializer,
    ValidationError,
)

from core.models import ChecklistItem, Membership, Task


def check_task_member(user, task):
    """Checklist é de quem participa do projeto da tarefa. Tarefa alheia: 404."""
    if not Membership.objects.filter(project=task.project_id, user=user).exists():
        raise NotFound('Tarefa não encontrada.')


class ChecklistItemListSerializer(ModelSerializer):
    class Meta:
        model = ChecklistItem
        fields = ('id', 'task', 'text', 'is_done', 'position')


class ChecklistItemRetrieveSerializer(ModelSerializer):
    class Meta:
        model = ChecklistItem
        fields = ('id', 'task', 'text', 'is_done', 'position', 'created_at')


class ChecklistItemCreateUpdateSerializer(ModelSerializer):
    """Cria, renomeia ou marca/desmarca um item. Sem position, o item novo entra no fim; a tarefa fica fixa."""

    class Meta:
        model = ChecklistItem
        fields = ('id', 'task', 'text', 'is_done', 'position')
        extra_kwargs = {'position': {'required': False}}

    def validate_task(self, task):
        if self.instance and task != self.instance.task:
            raise ValidationError('Não é possível mover o item para outra tarefa.')
        check_task_member(self.context['request'].user, task)
        return task

    def create(self, validated_data):
        if 'position' not in validated_data:
            last = validated_data['task'].checklist_items.aggregate(last=Max('position'))['last']
            validated_data['position'] = 0 if last is None else last + 1
        return super().create(validated_data)


class ChecklistItemReorderSerializer(Serializer):
    """Nova ordem do checklist: ids de TODOS os itens da tarefa, cada um uma única vez."""

    task = PrimaryKeyRelatedField(queryset=Task.objects.all())
    items = ListField(child=IntegerField(), allow_empty=False)

    def validate_task(self, task):
        check_task_member(self.context['request'].user, task)
        return task

    def validate(self, attrs):
        ids = attrs['items']
        task_ids = set(attrs['task'].checklist_items.values_list('pk', flat=True))
        if len(ids) != len(set(ids)) or set(ids) != task_ids:
            raise ValidationError({'items': 'Envie todos os itens do checklist da tarefa, cada um uma única vez.'})
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        items = {item.pk: item for item in validated_data['task'].checklist_items.select_for_update()}
        for position, pk in enumerate(validated_data['items']):
            items[pk].position = position
        ChecklistItem.objects.bulk_update(items.values(), ['position'])
        return sorted(items.values(), key=lambda item: item.position)
