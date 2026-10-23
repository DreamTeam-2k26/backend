from django.db import models

from .project import Project

COLUMN_NAME_MAX = 50


class Column(models.Model):
    """Coluna do Kanban. A category interna alimenta progresso e atraso, mesmo em colunas personalizadas."""

    class Category(models.TextChoices):
        TODO = 'todo', 'A fazer'
        IN_PROGRESS = 'in_progress', 'Em andamento'
        DONE = 'done', 'Concluído'

    # Colunas com que todo projeto nasce: (nome, categoria), na ordem do quadro.
    DEFAULTS = (
        ('A fazer', Category.TODO),
        ('Em andamento', Category.IN_PROGRESS),
        ('Concluído', Category.DONE),
    )

    project = models.ForeignKey(Project, related_name='columns', on_delete=models.CASCADE)
    name = models.CharField(max_length=COLUMN_NAME_MAX)
    category = models.CharField(max_length=20, choices=Category.choices)
    position = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Coluna'
        verbose_name_plural = 'Colunas'
        ordering = ('position', 'id')
        indexes = [models.Index(fields=['project', 'position'], name='idx_column_project_position')]

    def __str__(self):
        return f'{self.name} ({self.project})'

    @classmethod
    def create_defaults(cls, project):
        """Cria A fazer / Em andamento / Concluído. Não faz nada se o projeto já tiver colunas."""
        if project.columns.exists():
            return
        cls.objects.bulk_create(
            cls(project=project, name=name, category=category, position=position)
            for position, (name, category) in enumerate(cls.DEFAULTS)
        )
