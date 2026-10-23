from django.conf import settings
from django.db import models

ROOM_NAME_MAX = 64


class Room(models.Model):
    """Sala coletiva de conversa. Não existem conversas privadas 1:1: toda sala tem nome e participantes explícitos."""

    name = models.CharField(max_length=ROOM_NAME_MAX)
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
