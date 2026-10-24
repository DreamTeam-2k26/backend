"""Regras da transcrição das chamadas.

Cada navegador transcreve o próprio microfone e envia só as falas finalizadas pelo WebSocket da sala;
o servidor confere quem é (token), se a transcrição está ligada e guarda. Quem liga/desliga é o dono ou um
administrador, e todos na chamada são avisados (ver consumers.py). Quem lê é qualquer membro do projeto.
"""

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from chat.serializers.user import display_name

from . import services
from .models import Call, TranscriptSegment

MAX_TEXT = 1000  # uma fala contínua passa raramente de umas poucas frases
MAX_CLIENT_ID = 40
MAX_DURATION_MS = 5 * 60 * 1000


def is_active(call_id):
    """Transcrição ligada em uma chamada em andamento."""
    return Call.objects.filter(pk=call_id, status=Call.Status.LIVE, transcription_on=True).exists()


def set_enabled(call, user, enabled):
    """Liga ou desliga a transcrição de uma chamada em andamento (dono/admin)."""
    services.require_manager(user, call.project_id)

    if call.status != Call.Status.LIVE:
        raise ValidationError('A transcrição só pode ser alterada durante a chamada.')

    call.transcription_on = bool(enabled)
    call.save(update_fields=['transcription_on'])

    return call


def clean_text(raw):
    """Texto de uma fala: só string, espaços normalizados e tamanho limitado (nada do cliente é confiável)."""
    if not isinstance(raw, str):
        return ''

    return ' '.join(raw.split())[:MAX_TEXT]


def _clean_client_id(raw):
    return raw[:MAX_CLIENT_ID] if isinstance(raw, str) else ''


def _clean_duration(raw):
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return 0

    return int(min(max(raw, 0), MAX_DURATION_MS))


def add_segment(call_id, user, text, client_id='', duration_ms=0):
    """Guarda uma fala de quem está na chamada. Devolve o segmento, ou None se foi descartada.

    Descartes silenciosos: transcrição desligada, chamada encerrada, texto vazio, ou fala repetida (mesmo `client_id`).
    """
    text = clean_text(text)

    if not text:
        return None

    call = Call.objects.filter(pk=call_id, status=Call.Status.LIVE, transcription_on=True).first()

    if call is None or not services.is_member(user, call.project_id):
        return None

    now = timezone.now()
    started = call.started_at or now
    spoken_at = now.timestamp() - _clean_duration(duration_ms) / 1000
    offset_ms = max(0, int((spoken_at - started.timestamp()) * 1000))

    try:
        with transaction.atomic():
            return TranscriptSegment.objects.create(
                call=call,
                user=user,
                speaker_name=display_name(user)[:150],
                text=text,
                offset_ms=offset_ms,
                client_id=_clean_client_id(client_id),
            )
    except IntegrityError:
        return None


def segments_for(call, after=0):
    """Falas da chamada em ordem cronológica; `after` devolve só as de id maior (para atualizar sem repetir)."""
    return call.segments.filter(pk__gt=after).order_by('offset_ms', 'id')


def transcript_text(call):
    """Transcrição em texto puro (uma fala por linha), usada como entrada do resumo."""
    lines = []

    for segment in segments_for(call):
        lines.append(f'[{format_offset(segment.offset_ms)}] {segment.speaker_name}: {segment.text}')

    return '\n'.join(lines)


def format_offset(offset_ms):
    seconds = offset_ms // 1000
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)

    return f'{hours:d}:{minutes:02d}:{seconds:02d}' if hours else f'{minutes:02d}:{seconds:02d}'
