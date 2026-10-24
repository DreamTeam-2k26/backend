from django.db import transaction
from django.db.models import Count, Q
from rest_framework.serializers import IntegerField, Serializer

from core.models import BrainstormReply, BrainstormVote

from .brainstorm_topic import ensure_open


def vote_totals(target, user):
    """{score, upvotes, downvotes, my_vote} do tópico ou da resposta, do ponto de vista de ``user``."""
    totals = target.votes.aggregate(upvotes=Count('id', filter=Q(value=1)), downvotes=Count('id', filter=Q(value=-1)))
    mine = target.votes.filter(user=user).values_list('value', flat=True).first()
    return {
        'score': totals['upvotes'] - totals['downvotes'],
        'upvotes': totals['upvotes'],
        'downvotes': totals['downvotes'],
        'my_vote': mine or 0,
    }


class BrainstormVoteSerializer(Serializer):
    """Vota +1/-1 ou remove o voto (0). context['topic'] é a discussão do alvo (precisa estar aberta).

    save(user=, target=) devolve os totais atualizados (vote_totals).
    """

    value = IntegerField(min_value=-1, max_value=1, help_text='1 (concordo), -1 (discordo) ou 0 (remover voto).')

    def validate(self, attrs):
        ensure_open(self.context['topic'])
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        user, target, value = validated_data['user'], validated_data['target'], validated_data['value']
        lookup = {'user': user, 'reply' if isinstance(target, BrainstormReply) else 'topic': target}
        if value == 0:
            BrainstormVote.objects.filter(**lookup).delete()
        else:
            BrainstormVote.objects.update_or_create(**lookup, defaults={'value': value})
        return vote_totals(target, user)
