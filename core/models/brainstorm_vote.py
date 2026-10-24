from django.conf import settings
from django.db import models

from .brainstorm_reply import BrainstormReply
from .brainstorm_topic import BrainstormTopic


class BrainstormVote(models.Model):
    """Voto +1/-1 de um usuário no tópico OU em uma resposta (um por usuário e alvo). Remover = apagar a linha."""

    class Value(models.IntegerChoices):
        UP = 1, '+1'
        DOWN = -1, '-1'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='brainstorm_votes', on_delete=models.CASCADE)
    topic = models.ForeignKey(BrainstormTopic, related_name='votes', on_delete=models.CASCADE, null=True, blank=True)
    reply = models.ForeignKey(BrainstormReply, related_name='votes', on_delete=models.CASCADE, null=True, blank=True)
    value = models.SmallIntegerField(choices=Value.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Voto do Brainstorm'
        verbose_name_plural = 'Votos do Brainstorm'
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(topic__isnull=False, reply__isnull=True)
                    | models.Q(topic__isnull=True, reply__isnull=False)
                ),
                name='bs_vote_single_target',
            ),
            models.CheckConstraint(condition=models.Q(value__in=[1, -1]), name='bs_vote_value_pm_one'),
            models.UniqueConstraint(
                fields=['user', 'topic'], condition=models.Q(reply__isnull=True), name='uniq_bs_vote_user_topic'
            ),
            models.UniqueConstraint(fields=['user', 'reply'], name='uniq_bs_vote_user_reply'),
        ]

    def __str__(self):
        target = f'tópico {self.topic_id}' if self.topic_id else f'resposta {self.reply_id}'
        return f'{self.user} {self.value:+d} em {target}'
