from django.conf import settings
from django.db import models

from .project import Project
from .task import Task

SUPPORT_LINK_TITLE_MAX = 150
SUPPORT_LINK_URL_MAX = 2048


class SupportLink(models.Model):
    """Link externo de apoio do projeto; com task, é material daquela tarefa (sempre do mesmo projeto)."""

    project = models.ForeignKey(Project, related_name='support_links', on_delete=models.CASCADE)
    task = models.ForeignKey(Task, related_name='support_links', on_delete=models.CASCADE, null=True, blank=True)
    title = models.CharField(max_length=SUPPORT_LINK_TITLE_MAX)
    url = models.URLField(max_length=SUPPORT_LINK_URL_MAX)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='created_support_links',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Link de apoio'
        verbose_name_plural = 'Links de apoio'
        ordering = ('-created_at', '-id')

    def __str__(self):
        return self.title
