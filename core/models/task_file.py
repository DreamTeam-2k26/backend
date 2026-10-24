from django.conf import settings
from django.db import models

from .file import File
from .task import Task


class TaskFile(models.Model):
    """Arquivo do projeto anexado a uma tarefa (projects_task_files da modelagem).

    Só o vínculo: o arquivo físico continua um só, em File. Apagar a tarefa ou desvincular nunca apaga o arquivo;
    apagar o arquivo remove os vínculos. Tarefa e arquivo são do mesmo projeto (validado no serializer).
    """

    task = models.ForeignKey(Task, related_name='task_files', on_delete=models.CASCADE)
    file = models.ForeignKey(File, related_name='task_links', on_delete=models.CASCADE)
    attached_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='attached_task_files',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    attached_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Arquivo da tarefa'
        verbose_name_plural = 'Arquivos das tarefas'
        ordering = ('attached_at', 'id')
        constraints = [models.UniqueConstraint(fields=['task', 'file'], name='uq_task_files_task_file')]

    def __str__(self):
        return f'{self.file} em {self.task}'
