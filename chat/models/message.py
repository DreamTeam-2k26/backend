import re
import uuid
from pathlib import PurePath

from django.conf import settings
from django.db import models

from .room import Room

MESSAGE_MAX = 2000

# Somente estes tipos (detectados pelo conteúdo, nunca pela extensão) podem ser exibidos inline.
# SVG fica de fora de propósito: pode conter scripts.
INLINE_IMAGE_TYPES = frozenset({'image/jpeg', 'image/png', 'image/gif', 'image/webp'})


def message_file_path(_message, filename):
    """Nome aleatório no storage: o nome original fica em Message.file_name e o arquivo só sai pela API autorizada."""
    extension = re.sub(r'[^a-z0-9.]', '', PurePath(filename).suffix.lower())[:10]

    return f'chat_files/{uuid.uuid4().hex}{extension}'


class Message(models.Model):
    room = models.ForeignKey(Room, related_name='messages', on_delete=models.CASCADE)
    # SET_NULL: apagar um usuário da plataforma não pode destruir o histórico das salas.
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='chat_messages',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    body = models.CharField(max_length=MESSAGE_MAX, blank=True, null=True)
    file = models.FileField(upload_to=message_file_path, blank=True, null=True)
    file_name = models.CharField(max_length=255, blank=True, default='')
    file_size = models.PositiveBigIntegerField(null=True, blank=True)
    file_content_type = models.CharField(max_length=127, blank=True, default='')
    created = models.DateTimeField(auto_now_add=True)
    reply_to = models.ForeignKey('self', related_name='replies', on_delete=models.SET_NULL, null=True, blank=True)
    edited_at = models.DateTimeField(null=True, blank=True)
    is_deleted = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created', '-id']
        indexes = [models.Index(fields=['room', '-created'], name='chat_message_room_created_idx')]
        verbose_name = 'Mensagem'
        verbose_name_plural = 'Mensagens'

    def __str__(self):
        if self.body:
            return f'{self.author}: {self.body[:50]}'

        if self.file:
            return f'{self.author}: {self.file_name}'

        return f'{self.author}: (sem conteúdo)'

    @property
    def is_image(self):
        return self.file_content_type in INLINE_IMAGE_TYPES
