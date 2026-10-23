from django.db import models

from .task import Task

CHECKLIST_TEXT_MAX = 255


class ChecklistItem(models.Model):
    """Item do checklist de uma tarefa, em ordem de position."""

    task = models.ForeignKey(Task, related_name='checklist_items', on_delete=models.CASCADE)
    text = models.CharField(max_length=CHECKLIST_TEXT_MAX)
    is_done = models.BooleanField(default=False)
    position = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Item de checklist'
        verbose_name_plural = 'Itens de checklist'
        ordering = ('position', 'id')
        indexes = [models.Index(fields=['task', 'position'], name='idx_checklistitem_task_pos')]

    def __str__(self):
        return self.text
