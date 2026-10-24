from django.conf import settings
from django.db import models
from django.db.models import Q

TITLE_MAX = 150


class Call(models.Model):
    """Chamada de voz/vídeo de um projeto: agendada, em andamento ou encerrada.

    A mídia trafega direto entre os navegadores (WebRTC); o servidor só guarda o estado da chamada,
    quem está nela e repassa a sinalização (ver consumers.py).
    """

    class Status(models.TextChoices):
        SCHEDULED = 'scheduled', 'Agendada'
        LIVE = 'live', 'Em andamento'
        ENDED = 'ended', 'Encerrada'

    project = models.ForeignKey('core.Project', related_name='calls', on_delete=models.CASCADE)
    title = models.CharField(max_length=TITLE_MAX)
    description = models.TextField(blank=True, default='')
    scheduled_for = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.SCHEDULED)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name='created_calls', on_delete=models.SET_NULL, null=True, blank=True
    )
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # Transcrição (ver transcription.py): ligada pelo dono/admin durante a chamada; o resumo é gerado depois.
    transcription_on = models.BooleanField(default=False)
    summary = models.TextField(blank=True, default='')
    summary_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Chamada'
        verbose_name_plural = 'Chamadas'
        ordering = ('-created_at', '-id')
        indexes = [models.Index(fields=['project', 'status'], name='idx_call_project_status')]
        constraints = [
            models.UniqueConstraint(
                fields=['project'], condition=Q(status='live'), name='uq_call_one_live_per_project'
            ),
        ]

    def __str__(self):
        return self.title


class Participant(models.Model):
    """Quem está (ou esteve) na chamada. `left_at` vazio = está dentro agora.

    `channel_name` identifica a conexão atual: entrar de outra aba assume o lugar da anterior.
    `state` guarda microfone/câmera/tela/mão para quem chega depois ver o estado atual.
    """

    call = models.ForeignKey(Call, related_name='participants', on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='call_participations', on_delete=models.CASCADE)
    channel_name = models.CharField(max_length=255, blank=True, default='')
    state = models.JSONField(default=dict, blank=True)
    joined_at = models.DateTimeField()
    left_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Participante da chamada'
        verbose_name_plural = 'Participantes da chamada'
        ordering = ('joined_at', 'id')
        constraints = [models.UniqueConstraint(fields=['call', 'user'], name='uq_call_participant')]

    def __str__(self):
        return f'{self.user_id} em {self.call_id}'


class TranscriptSegment(models.Model):
    """Uma fala transcrita. Cada navegador transcreve o próprio microfone, então o autor vem da conexão
    (token), nunca do conteúdo da mensagem. `speaker_name` guarda o nome na hora, para o histórico não mudar.

    `offset_ms` é o instante da fala em relação ao início da chamada; `client_id` evita duplicar uma fala
    reenviada depois de uma queda de conexão.
    """

    call = models.ForeignKey(Call, related_name='segments', on_delete=models.CASCADE)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name='call_segments', on_delete=models.SET_NULL, null=True, blank=True
    )
    speaker_name = models.CharField(max_length=150)
    text = models.TextField()
    offset_ms = models.PositiveIntegerField(default=0)
    client_id = models.CharField(max_length=40, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Fala transcrita'
        verbose_name_plural = 'Falas transcritas'
        ordering = ('offset_ms', 'id')
        indexes = [models.Index(fields=['call', 'id'], name='idx_segment_call')]
        constraints = [
            models.UniqueConstraint(
                fields=['call', 'user', 'client_id'],
                condition=~Q(client_id=''),
                name='uq_segment_client_id',
            ),
        ]

    def __str__(self):
        return f'{self.speaker_name}: {self.text[:40]}'
