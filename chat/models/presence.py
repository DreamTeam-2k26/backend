from django.conf import settings
from django.db import models


class UserPresence(models.Model):
    """Presença no chat. Fica no app do chat para não alterar o modelo oficial de usuário."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, related_name='chat_presence', on_delete=models.CASCADE)
    # Uma conexão WebSocket por aba aberta; o usuário está online enquanto houver pelo menos uma.
    connections = models.PositiveIntegerField(default=0)
    last_seen = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Presença'
        verbose_name_plural = 'Presenças'

    def __str__(self):
        return f'{self.user} ({"online" if self.is_online else "offline"})'

    @property
    def is_online(self):
        return self.connections > 0
