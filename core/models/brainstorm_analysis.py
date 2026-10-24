from django.conf import settings
from django.db import models

from .brainstorm_topic import BrainstormTopic


class BrainstormAnalysis(models.Model):
    """Síntese da IA para um ciclo da discussão. Cada pedido cria uma linha nova (anteriores são preservadas).

    result guarda o JSON já validado (main_solution, reasoning, alternatives, sources, ai_additions,
    task_suggestions). error é uma mensagem segura para o usuário, nunca a resposta crua do provedor.
    """

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pendente'
        RUNNING = 'running', 'Em execução'
        READY = 'ready', 'Pronta'
        FAILED = 'failed', 'Falhou'

    topic = models.ForeignKey(BrainstormTopic, related_name='analyses', on_delete=models.CASCADE)
    cycle = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    result = models.JSONField(null=True, blank=True)
    model = models.CharField(max_length=100, blank=True, default='')
    error = models.CharField(max_length=300, blank=True, default='')
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='brainstorm_analyses',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Análise do Brainstorm'
        verbose_name_plural = 'Análises do Brainstorm'
        ordering = ('-created_at', '-id')
        indexes = [models.Index(fields=['topic', 'cycle'], name='idx_bs_analysis_topic_cycle')]

    def __str__(self):
        return f'Análise {self.pk} de "{self.topic}" (ciclo {self.cycle}, {self.status})'
