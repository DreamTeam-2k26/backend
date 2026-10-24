from rest_framework.exceptions import NotFound
from rest_framework.serializers import CharField, IntegerField, ModelSerializer, ValidationError

from core.models import BrainstormReply, Membership

from .brainstorm_topic import VOTE_FIELDS, ensure_open


class BrainstormReplyListSerializer(ModelSerializer):
    author_name = CharField(source='author.name', read_only=True, default=None)
    score = IntegerField(read_only=True)  # votos anotados pela BrainstormReplyViewSet
    upvotes = IntegerField(read_only=True)
    downvotes = IntegerField(read_only=True)
    my_vote = IntegerField(read_only=True, help_text='Voto do usuário logado: 1, -1 ou 0 (sem voto).')

    class Meta:
        model = BrainstormReply
        fields = (
            'id',
            'topic',
            'parent',
            'author',
            'author_name',
            'content',
            *VOTE_FIELDS,
            'created_at',
            'updated_at',
        )


class BrainstormReplyCreateUpdateSerializer(ModelSerializer):
    """Responde à discussão (ou a outra resposta via parent, do mesmo tópico). Só com a discussão aberta.

    author vem do perform_create; tópico e parent ficam fixos depois de criada.
    """

    class Meta:
        model = BrainstormReply
        fields = ('id', 'topic', 'parent', 'content')

    def validate_topic(self, topic):
        if self.instance and topic != self.instance.topic:
            raise ValidationError('Não é possível mover a resposta para outra discussão.')
        if not Membership.objects.filter(project=topic.project_id, user=self.context['request'].user).exists():
            raise NotFound('Discussão não encontrada.')
        return topic

    def validate(self, attrs):
        topic = attrs.get('topic') or self.instance.topic
        ensure_open(topic)
        parent = attrs.get('parent')
        if self.instance and 'parent' in attrs and parent != self.instance.parent:
            raise ValidationError({'parent': 'Não é possível mudar a resposta de lugar na conversa.'})
        if parent is not None and parent.topic_id != topic.pk:
            raise ValidationError({'parent': 'A resposta precisa ser da mesma discussão.'})
        return attrs
