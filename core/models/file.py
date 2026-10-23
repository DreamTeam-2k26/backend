import re
import uuid
from pathlib import PurePath

from django.conf import settings
from django.db import models
from django.db.models.functions import Lower

from .folder import Folder
from .project import Project

FILE_NAME_MAX = 255

OOXML = 'application/vnd.openxmlformats-officedocument'
OLE = {'application/CDFV2', 'application/x-ole-storage'}

# Extensão permitida -> tipos aceitos, detectados pelo CONTEÚDO (libmagic), nunca pelo nome ou pelo cliente.
# Um arquivo só entra se a extensão estiver aqui e o conteúdo combinar com ela (ex.: .exe renomeado para .pdf
# é recusado). Fora de propósito: html, svg, scripts, executáveis e compactados (podem esconder qualquer coisa).
ALLOWED_FILE_TYPES = {
    '.pdf': {'application/pdf'},
    '.png': {'image/png'},
    '.jpg': {'image/jpeg'},
    '.jpeg': {'image/jpeg'},
    '.gif': {'image/gif'},
    '.webp': {'image/webp'},
    '.txt': {'text/plain'},
    '.md': {'text/plain', 'text/markdown'},
    '.csv': {'text/plain', 'text/csv', 'application/csv'},
    '.docx': {f'{OOXML}.wordprocessingml.document', 'application/zip'},
    '.xlsx': {f'{OOXML}.spreadsheetml.sheet', 'application/zip'},
    '.pptx': {f'{OOXML}.presentationml.presentation', 'application/zip'},
    '.doc': {'application/msword', *OLE},
    '.xls': {'application/vnd.ms-excel', *OLE},
    '.ppt': {'application/vnd.ms-powerpoint', *OLE},
    '.odt': {'application/vnd.oasis.opendocument.text', 'application/zip'},
    '.ods': {'application/vnd.oasis.opendocument.spreadsheet', 'application/zip'},
    '.odp': {'application/vnd.oasis.opendocument.presentation', 'application/zip'},
}


def file_extension(filename):
    """Extensão em minúsculas só com letras/números (ex.: '.pdf'); vazia se não houver."""
    suffix = re.sub(r'[^a-z0-9]', '', PurePath(filename).suffix.lower())[:10]
    return f'.{suffix}' if suffix else ''


def project_file_path(file, filename):
    """Caminho aleatório no storage, dentro da pasta do projeto.

    O nome enviado pelo cliente nunca entra no caminho (evita path traversal e nomes adivinháveis): o nome
    exibido fica em File.name e o arquivo só sai pelo download autorizado da API.
    """
    return f'project_files/{file.project_id}/{uuid.uuid4().hex}{file_extension(filename)}'


class File(models.Model):
    """Arquivo de um projeto, opcionalmente dentro de uma pasta (folder vazio = raiz).

    Usa o storage padrão do projeto (disco ou Cloudinary), sem relação com uploader.Image/Document, que
    continuam servindo só a foto de perfil e os uploads próprios do uploader.
    """

    project = models.ForeignKey(Project, related_name='files', on_delete=models.CASCADE)
    folder = models.ForeignKey(Folder, related_name='files', on_delete=models.CASCADE, null=True, blank=True)
    name = models.CharField(max_length=FILE_NAME_MAX)
    file = models.FileField(upload_to=project_file_path, max_length=255)
    size = models.PositiveBigIntegerField()
    mime_type = models.CharField(max_length=100)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='uploaded_files',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Arquivo'
        verbose_name_plural = 'Arquivos'
        ordering = (Lower('name'), 'id')
        indexes = [models.Index(fields=['project', 'folder'], name='idx_file_project_folder')]

    def __str__(self):
        return self.name
