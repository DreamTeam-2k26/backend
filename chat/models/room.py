from django.conf import settings
from django.db import models

ROOM_NAME_MAX = 100  # igual ao limite do nome do projeto (core.Project.name)


class Room(models.Model):
    """Sala de conversa de um projeto. Não existem conversas privadas 1:1.

    Cada projeto tem exatamente uma sala (`project`); nome e participantes seguem o projeto (ver
    chat/project_rooms.py). `project` fica nulo só nas salas avulsas antigas, criadas antes dos projetos.
    """

    name = models.CharField(max_length=ROOM_NAME_MAX)
    project = models.OneToOneField(
        'core.Project',
        related_name='chat_room',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    # Mesmo padrão da foto de perfil: a imagem é enviada ao uploader e vinculada pelo attachment_key.
    image = models.ForeignKey(
        'uploader.Image',
        related_name='+',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='created_chat_rooms',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_message_at = models.DateTimeField(null=True, blank=True)
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        through='Membership',
        related_name='chat_rooms',
        blank=True,
    )

    class Meta:
        verbose_name = 'Sala de chat'
        verbose_name_plural = 'Salas de chat'

    def __str__(self):
        return self.name


class Membership(models.Model):
    """Participação de um usuário em uma sala: é a única fonte de autorização do chat."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='chat_memberships', on_delete=models.CASCADE)
    room = models.ForeignKey(Room, related_name='memberships', on_delete=models.CASCADE)
    is_admin = models.BooleanField(default=False)
    joined_at = models.DateTimeField(auto_now_add=True)
    last_read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Participação'
        verbose_name_plural = 'Participações'
        constraints = [
            models.UniqueConstraint(fields=['user', 'room'], name='chat_membership_unique_user_room'),
        ]

    def __str__(self):
        return f'{self.user} em {self.room}'
