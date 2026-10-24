from rest_framework.exceptions import NotFound
from rest_framework.serializers import (
    BooleanField,
    CharField,
    IntegerField,
    ModelSerializer,
    ValidationError,
)

from core.models import BrainstormDecision, BrainstormTopic, Membership

# Campos de voto anotados pela BrainstormTopicViewSet (e pela BrainstormReplyViewSet nas respostas).
VOTE_FIELDS = ('score', 'upvotes', 'downvotes', 'my_vote')


def ensure_open(topic):
    """Respostas, votos e edições só com a discussão aberta."""
    if topic.status == BrainstormTopic.Status.ARCHIVED:
        raise ValidationError('A discussão está arquivada e é somente leitura.')
    if topic.status != BrainstormTopic.Status.OPEN:
        raise ValidationError('A discussão está encerrada. Peça ao dono ou a um administrador para reabri-la.')


class BrainstormDecisionSummarySerializer(ModelSerializer):
    approved_by_name = CharField(source='approved_by.name', read_only=True, default=None)

    class Meta:
        model = BrainstormDecision
        fields = ('id', 'cycle', 'text', 'approved_by', 'approved_by_name', 'approved_at')


class BrainstormTopicListSerializer(ModelSerializer):
    author_name = CharField(source='author.name', read_only=True, default=None)
    replies_count = IntegerField(read_only=True)
    score = IntegerField(read_only=True)
    upvotes = IntegerField(read_only=True)
    downvotes = IntegerField(read_only=True)
    my_vote = IntegerField(read_only=True, help_text='Voto do usuário logado: 1, -1 ou 0 (sem voto).')
    in_review = BooleanField(read_only=True, help_text='Reaberta com a decisão anterior ainda vigente.')

    class Meta:
        model = BrainstormTopic
        fields = (
            'id',
            'project',
            'title',
            'context',  # prévia no card da lista (o frontend corta o texto)
            'category',
            'status',
            'author',
            'author_name',
            'replies_count',
            *VOTE_FIELDS,
            'cycle',
            'in_review',
            'created_at',
            'updated_at',
            'closed_at',
        )


class BrainstormTopicRetrieveSerializer(BrainstormTopicListSerializer):
    current_decision = BrainstormDecisionSummarySerializer(read_only=True)

    class Meta(BrainstormTopicListSerializer.Meta):
        fields = (
            *BrainstormTopicListSerializer.Meta.fields,
            'initial_suggestion',
            'archived_at',
            'current_decision',
        )


class BrainstormTopicCreateUpdateSerializer(ModelSerializer):
    """Cria/edita discussão. author vem do perform_create; o projeto fica fixo; editar só com ela aberta."""

    class Meta:
        model = BrainstormTopic
        fields = ('id', 'project', 'title', 'category', 'context', 'initial_suggestion')

    def validate_project(self, project):
        if self.instance and project != self.instance.project:
            raise ValidationError('Não é possível mover a discussão para outro projeto.')
        if not Membership.objects.filter(project=project, user=self.context['request'].user).exists():
            raise NotFound('Projeto não encontrado.')
        return project

    def validate(self, attrs):
        if self.instance:
            ensure_open(self.instance)
        return attrs
