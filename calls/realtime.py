"""Eventos das chamadas pelo channel layer.

- `calls_user_<id>`: um grupo por usuário, ouvido pelo CallNotifyConsumer (avisos de chamada nova/encerrada).
- `call_<id>`: a sala de uma chamada, ouvida pelos CallRoomConsumer dos participantes.
Funções síncronas: views chamam direto; consumers, via database_sync_to_async.
"""

import json

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction
from rest_framework.renderers import JSONRenderer

from . import services


def user_group(user_id):
    return f'calls_user_{user_id}'


def room_group(call_id):
    return f'call_{call_id}'


def _json(data):
    return json.loads(JSONRenderer().render(data))


def _send(group, event):
    async_to_sync(get_channel_layer().group_send)(group, event)


def broadcast_call_changed(call, *, removed=False):
    """Avisa todos os participantes do projeto que a chamada mudou (iniciou, agendou, encerrou, editou, removeu)."""
    from .serializers import CallSerializer  # noqa: PLC0415 - evita import circular

    payload = _json(CallSerializer(call).data)
    user_ids = services.member_ids(call.project_id)

    def send():
        for user_id in user_ids:
            _send(user_group(user_id), {'type': 'call.changed', 'call': payload, 'removed': removed})

    transaction.on_commit(send)


def broadcast_transcription_changed(call, user_id):
    """Avisa quem está na sala que a transcrição foi ligada/desligada (e por quem)."""
    event = {'type': 'transcription.changed', 'enabled': call.transcription_on, 'by': user_id}
    transaction.on_commit(lambda: _send(room_group(call.pk), event))


def broadcast_call_ended(call_id):
    """Fecha a sala: todos os conectados recebem `call.ended` e saem."""
    transaction.on_commit(lambda: _send(room_group(call_id), {'type': 'call.ended'}))
