from django.conf import settings
from django.db import models
from django.utils import timezone

from .column import Column
from .project import Project

TASK_TITLE_MAX = 200


class Task(models.Model):
    """Tarefa do Kanban. Coluna e responsável são sempre do mesmo projeto (validado no serializer)."""

    class Priority(models.TextChoices):
        LOW = 'low', 'Baixa'
        MEDIUM = 'medium', 'Média'
        HIGH = 'high', 'Alta'

    # project é redundante com column.project de propósito: simplifica filtros e checagem de acesso.
    project = models.ForeignKey(Project, related_name='tasks', on_delete=models.CASCADE)
    # RESTRICT: coluna com tarefas não é excluída sozinha (a ColumnViewSet devolve 400), mas o projeto inteiro
    # pode ser excluído em cascata. PROTECT bloquearia até a exclusão do projeto.
    column = models.ForeignKey(Column, related_name='tasks', on_delete=models.RESTRICT)
    title = models.CharField(max_length=TASK_TITLE_MAX)
    description = models.TextField(blank=True, default='')
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.MEDIUM)
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='assigned_tasks',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    due_date = models.DateField(null=True, blank=True)
    position = models.PositiveIntegerField(default=0)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='created_tasks',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Tarefa'
        verbose_name_plural = 'Tarefas'
        ordering = ('column__position', 'position', 'id')
        indexes = [
            models.Index(fields=['project', 'column', 'position'], name='idx_task_project_column_pos'),
            models.Index(fields=['project', 'due_date'], name='idx_task_project_due_date'),
            models.Index(fields=['project', 'priority'], name='idx_task_project_priority'),
        ]

    def __str__(self):
        return self.title

    @property
    def is_overdue(self):
        """Atrasada: prazo já passou e a tarefa não está numa coluna de concluídas."""
        return (
            self.due_date is not None
            and self.due_date < timezone.localdate()
            and self.column.category != Column.Category.DONE
        )

    def save(self, *args, **kwargs):
        # completed_at acompanha a categoria da coluna: entra em done marca a data, sai de done limpa.
        if self.column.category == Column.Category.DONE:
            self.completed_at = self.completed_at or timezone.now()
        else:
            self.completed_at = None
        super().save(*args, **kwargs)
