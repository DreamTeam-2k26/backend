from django.conf import settings
from django.db import models

from .project import Project

TOPIC_TITLE_MAX = 150


class BrainstormTopic(models.Model):
    """Discussão do Brainstorm de um projeto.

    cycle é a versão da discussão: começa em 1 e sobe a cada reabertura. current_decision é a decisão vigente;
    quando ela é de um ciclo anterior ao atual, a discussão está "Em revisão" (in_review).
    """

    class Category(models.TextChoices):
        IDEAS = 'ideas', 'Ideias'
        QUESTIONS = 'questions', 'Dúvidas'
        PLANNING = 'planning', 'Planejamento'
        DECISIONS = 'decisions', 'Decisões'

    class Status(models.TextChoices):
        OPEN = 'open', 'Em discussão'
        CLOSED = 'closed', 'Encerrada'
        DECIDED = 'decided', 'Decisão aprovada'
        ARCHIVED = 'archived', 'Arquivada'

    project = models.ForeignKey(Project, related_name='brainstorm_topics', on_delete=models.CASCADE)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='brainstorm_topics',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    title = models.CharField(max_length=TOPIC_TITLE_MAX)
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.IDEAS)
    context = models.TextField()
    initial_suggestion = models.TextField(blank=True, default='')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    cycle = models.PositiveIntegerField(default=1)
    current_decision = models.ForeignKey(
        'BrainstormDecision',
        related_name='+',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Discussão do Brainstorm'
        verbose_name_plural = 'Discussões do Brainstorm'
        ordering = ('-created_at', '-id')
        indexes = [
            models.Index(fields=['project', 'status'], name='idx_bs_topic_project_status'),
            models.Index(fields=['project', 'category'], name='idx_bs_topic_project_category'),
            models.Index(fields=['project', 'created_at'], name='idx_bs_topic_project_created'),
        ]

    def __str__(self):
        return self.title

    @property
    def in_review(self):
        """Reaberta com uma decisão antiga ainda vigente (até a aprovação da nova versão)."""
        return self.current_decision is not None and self.current_decision.cycle < self.cycle

    def has_interactions(self):
        """Respostas, votos, análises ou decisões impedem a exclusão (a discussão deve ser arquivada)."""
        return self.replies.exists() or self.votes.exists() or self.analyses.exists() or self.decisions.exists()
