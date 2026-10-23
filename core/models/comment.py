from django.conf import settings
from django.db import models

from .task import Task


class Comment(models.Model):
    """Comentário em uma tarefa. Se o autor for apagado do sistema, o comentário fica (author = NULL)."""

    task = models.ForeignKey(Task, related_name='comments', on_delete=models.CASCADE)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='task_comments',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Comentário'
        verbose_name_plural = 'Comentários'
        ordering = ('created_at', 'id')
        indexes = [models.Index(fields=['task', 'created_at'], name='idx_comment_task_created')]

    def __str__(self):
        return f'{self.author or "Usuário removido"}: {self.body[:50]}'
