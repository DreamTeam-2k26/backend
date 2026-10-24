from django.conf import settings
from django.db import models

from .brainstorm_analysis import BrainstormAnalysis
from .brainstorm_topic import BrainstormTopic
from .task import TASK_TITLE_MAX, Task


class BrainstormDecision(models.Model):
    """Decisão aprovada por dono/admin para um ciclo. Uma por (tópico, ciclo): reenviar não duplica.

    Sem endpoint de edição: as versões aprovadas formam um histórico imutável.
    """

    topic = models.ForeignKey(BrainstormTopic, related_name='decisions', on_delete=models.CASCADE)
    cycle = models.PositiveIntegerField()
    # RESTRICT: a análise que embasou uma decisão não some sozinha, mas o tópico/projeto inteiro pode ser excluído
    # em cascata (PROTECT bloquearia até a exclusão do projeto).
    analysis = models.ForeignKey(BrainstormAnalysis, related_name='decisions', on_delete=models.RESTRICT)
    text = models.TextField()
    justification = models.TextField(blank=True, default='')
    rejected_suggestions = models.JSONField(default=list, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='brainstorm_decisions',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    approved_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Decisão do Brainstorm'
        verbose_name_plural = 'Decisões do Brainstorm'
        ordering = ('cycle', 'id')
        constraints = [models.UniqueConstraint(fields=['topic', 'cycle'], name='uniq_bs_decision_topic_cycle')]

    def __str__(self):
        return f'Decisão v{self.cycle} de "{self.topic}"'


class BrainstormDecisionTask(models.Model):
    """Tarefa criada (created=True) ou só vinculada por uma decisão. title é a cópia do momento da aprovação."""

    decision = models.ForeignKey(BrainstormDecision, related_name='task_links', on_delete=models.CASCADE)
    task = models.ForeignKey(Task, related_name='brainstorm_links', on_delete=models.SET_NULL, null=True, blank=True)
    suggestion_index = models.PositiveIntegerField(null=True, blank=True)
    title = models.CharField(max_length=TASK_TITLE_MAX)
    created = models.BooleanField(default=False)

    class Meta:
        verbose_name = 'Tarefa da decisão'
        verbose_name_plural = 'Tarefas da decisão'
        ordering = ('id',)
        constraints = [models.UniqueConstraint(fields=['decision', 'task'], name='uniq_bs_decision_task')]

    def __str__(self):
        return self.title
