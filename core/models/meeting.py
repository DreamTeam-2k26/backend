from django.conf import settings
from django.db import models
from django.db.models import F, Q

from .project import Project

MEETING_TITLE_MAX = 150
MEETING_URL_MAX = 2048


class Meeting(models.Model):
    """Reunião do projeto com link externo (Meet, Discord etc.); não há videoconferência nativa."""

    project = models.ForeignKey(Project, related_name='meetings', on_delete=models.CASCADE)
    title = models.CharField(max_length=MEETING_TITLE_MAX)
    description = models.TextField(blank=True, default='')
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField(null=True, blank=True)
    url = models.URLField(max_length=MEETING_URL_MAX)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='created_meetings',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Reunião'
        verbose_name_plural = 'Reuniões'
        ordering = ('starts_at', 'id')
        indexes = [models.Index(fields=['project', 'starts_at'], name='idx_meeting_project_starts')]
        constraints = [
            models.CheckConstraint(
                condition=Q(ends_at__isnull=True) | Q(ends_at__gte=F('starts_at')),
                name='ck_meeting_ends_after_starts',
            ),
        ]

    def __str__(self):
        return self.title
