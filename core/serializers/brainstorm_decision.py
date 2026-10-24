from django.db import transaction
from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.serializers import (
    BooleanField,
    CharField,
    ChoiceField,
    DateField,
    IntegerField,
    ModelSerializer,
    Serializer,
    ValidationError,
)

from core.models import (
    BrainstormAnalysis,
    BrainstormDecision,
    BrainstormDecisionTask,
    BrainstormTopic,
    Column,
    Task,
)
from core.models.task import TASK_TITLE_MAX

from .brainstorm_analysis import BrainstormAnalysisSummarySerializer
from .task import TaskCreateUpdateSerializer

DECISION_TEXT_MAX = 10_000


class DecisionConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = (
        'Este ciclo já tem uma decisão aprovada com outra análise. Reabra a discussão para uma nova versão.'
    )
    default_code = 'decision_conflict'


class BrainstormDecisionTaskSerializer(ModelSerializer):
    class Meta:
        model = BrainstormDecisionTask
        fields = ('task', 'title', 'created', 'suggestion_index')


class BrainstormDecisionSerializer(ModelSerializer):
    approved_by_name = CharField(source='approved_by.name', read_only=True, default=None)
    tasks = BrainstormDecisionTaskSerializer(source='task_links', many=True, read_only=True)

    class Meta:
        model = BrainstormDecision
        fields = (
            'id',
            'topic',
            'cycle',
            'analysis',
            'text',
            'justification',
            'rejected_suggestions',
            'approved_by',
            'approved_by_name',
            'approved_at',
            'tasks',
        )


class BrainstormVersionSerializer(Serializer):
    """Um ciclo da discussão: as análises pedidas e a decisão aprovada (null se ainda não houver)."""

    cycle = IntegerField()
    current = BooleanField(help_text='A decisão deste ciclo é a vigente.')
    analyses = BrainstormAnalysisSummarySerializer(many=True)
    decision = BrainstormDecisionSerializer(allow_null=True)


class BrainstormApproveTaskSerializer(Serializer):
    """Uma proposta aprovada: tarefa nova (title + campos, editáveis) ou existing_task_id do mesmo projeto."""

    suggestion_index = IntegerField(min_value=0, required=False, allow_null=True)
    existing_task_id = IntegerField(required=False, allow_null=True)
    title = CharField(max_length=TASK_TITLE_MAX, required=False, allow_blank=True)
    description = CharField(required=False, allow_blank=True, default='')
    priority = ChoiceField(choices=Task.Priority.choices, required=False, default=Task.Priority.MEDIUM)
    assignee = IntegerField(required=False, allow_null=True)
    due_date = DateField(required=False, allow_null=True)

    def validate(self, attrs):
        if attrs.get('existing_task_id') is None and not attrs.get('title', '').strip():
            raise ValidationError('Informe o título da tarefa nova ou existing_task_id de uma tarefa existente.')
        return attrs


class BrainstormApproveSerializer(Serializer):
    """Aprova a decisão do ciclo atual de context['topic'] (dono/admin) e cria/vincula as tarefas escolhidas.

    Tudo numa transação, com a discussão travada. Reenviar com a mesma análise devolve a decisão já gravada
    (self.replayed = True) sem criar nada; outra análise no mesmo ciclo responde 409.
    """

    analysis = IntegerField()
    text = CharField(max_length=DECISION_TEXT_MAX)
    justification = CharField(required=False, allow_blank=True, default='')
    tasks = BrainstormApproveTaskSerializer(many=True, required=False, default=list)

    replayed = False

    @transaction.atomic
    def create(self, validated_data):
        topic = BrainstormTopic.objects.select_for_update().get(pk=self.context['topic'].pk)
        analysis = topic.analyses.filter(pk=validated_data['analysis']).first()
        if analysis is None:
            raise ValidationError({'analysis': 'Análise não encontrada nesta discussão.'})

        existing = topic.decisions.filter(cycle=topic.cycle).first()
        if existing is not None:
            if existing.analysis_id != analysis.pk:
                raise DecisionConflict
            self.replayed = True
            return existing

        if topic.status != BrainstormTopic.Status.CLOSED:
            raise ValidationError('A decisão só pode ser aprovada com a discussão encerrada.')
        if analysis.cycle != topic.cycle or analysis.status != BrainstormAnalysis.Status.READY:
            raise ValidationError({'analysis': 'Use uma análise pronta do ciclo atual da discussão.'})

        items = validated_data['tasks']
        suggestions = (analysis.result or {}).get('task_suggestions', [])
        self.check_items(topic, items, len(suggestions))

        decision = BrainstormDecision.objects.create(
            topic=topic,
            cycle=topic.cycle,
            analysis=analysis,
            text=validated_data['text'],
            justification=validated_data['justification'],
            approved_by=self.context['request'].user,
            rejected_suggestions=[
                index
                for index in range(len(suggestions))
                if index not in {item.get('suggestion_index') for item in items}
            ],
        )
        for position, item in enumerate(items):
            self.link_task(decision, item, position)

        topic.status = BrainstormTopic.Status.DECIDED
        topic.current_decision = decision
        topic.save(update_fields=['status', 'current_decision', 'updated_at'])
        return decision

    def check_items(self, topic, items, suggestions_count):
        existing_ids = [item['existing_task_id'] for item in items if item.get('existing_task_id') is not None]
        if len(existing_ids) != len(set(existing_ids)):
            raise ValidationError({'tasks': 'A mesma tarefa existente foi escolhida mais de uma vez.'})
        found = set(Task.objects.filter(project=topic.project_id, pk__in=existing_ids).values_list('pk', flat=True))
        if found != set(existing_ids):
            raise ValidationError({'tasks': 'Só é possível vincular tarefas do mesmo projeto.'})
        indexes = [item['suggestion_index'] for item in items if item.get('suggestion_index') is not None]
        if any(index >= suggestions_count for index in indexes) or len(indexes) != len(set(indexes)):
            raise ValidationError({'tasks': 'suggestion_index não corresponde a uma sugestão desta análise.'})
        if any(item.get('existing_task_id') is None for item in items):
            self.column = topic.project.columns.filter(category=Column.Category.TODO).order_by('position', 'id').first()
            if self.column is None:
                raise ValidationError({'tasks': 'O projeto não tem uma coluna "A fazer" para receber as tarefas.'})

    def link_task(self, decision, item, position):
        if item.get('existing_task_id') is not None:
            task = Task.objects.get(pk=item['existing_task_id'])
            created = False
        else:
            # Mesmas regras do Kanban (responsável participante, prioridade, prazo a partir de hoje).
            serializer = TaskCreateUpdateSerializer(
                data={
                    'project': decision.topic.project_id,
                    'column': self.column.pk,
                    'title': item['title'].strip(),
                    'description': item['description'],
                    'priority': item['priority'],
                    'assignee': item.get('assignee'),
                    'due_date': item.get('due_date'),
                },
                context=self.context,
            )
            if not serializer.is_valid():
                raise ValidationError({'tasks': {position: serializer.errors}})
            task = serializer.save(created_by=self.context['request'].user)
            created = True
        BrainstormDecisionTask.objects.create(
            decision=decision,
            task=task,
            suggestion_index=item.get('suggestion_index'),
            title=task.title,
            created=created,
        )
