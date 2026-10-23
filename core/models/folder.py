from django.conf import settings
from django.db import models
from django.db.models.functions import Lower

from .project import Project

FOLDER_NAME_MAX = 100


class Folder(models.Model):
    """Pasta de arquivos do projeto. parent vazio = raiz; excluir uma pasta exclui todas as subpastas.

    Nome único por pasta-pai e ausência de ciclos são validados no serializer (o UNIQUE do banco não
    protegeria a raiz, porque parent é NULL).
    """

    project = models.ForeignKey(Project, related_name='folders', on_delete=models.CASCADE)
    parent = models.ForeignKey('self', related_name='children', on_delete=models.CASCADE, null=True, blank=True)
    name = models.CharField(max_length=FOLDER_NAME_MAX)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='created_folders',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Pasta'
        verbose_name_plural = 'Pastas'
        ordering = (Lower('name'), 'id')
        indexes = [models.Index(fields=['project', 'parent'], name='idx_folder_project_parent')]

    def __str__(self):
        return self.name

    def ancestors_and_self(self):
        """Caminho da raiz até esta pasta (para o breadcrumb). Uma consulta por nível.

        Para se o caminho repetir uma pasta: um ciclo gravado por fora da API não trava a requisição.
        """
        path = []
        seen = set()
        folder = self
        while folder is not None and folder.pk not in seen:
            seen.add(folder.pk)
            path.append(folder)
            folder = folder.parent
        return path[::-1]
