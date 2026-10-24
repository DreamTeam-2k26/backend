from django.conf import settings
from django.db import models

from .brainstorm_topic import BrainstormTopic

REPLY_CONTENT_MAX = 5000


class BrainstormReply(models.Model):
    """Resposta de uma discussão. parent (opcional, do mesmo tópico) forma a thread."""

    topic = models.ForeignKey(BrainstormTopic, related_name='replies', on_delete=models.CASCADE)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='brainstorm_replies',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    parent = models.ForeignKey('self', related_name='children', on_delete=models.CASCADE, null=True, blank=True)
    content = models.TextField(max_length=REPLY_CONTENT_MAX)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Resposta do Brainstorm'
        verbose_name_plural = 'Respostas do Brainstorm'
        ordering = ('created_at', 'id')
        indexes = [models.Index(fields=['topic', 'created_at'], name='idx_bs_reply_topic_created')]

    def __str__(self):
        return f'{self.author or "Usuário removido"}: {self.content[:50]}'
