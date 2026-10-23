"""Eventos do chat enviados pelo channel layer (um grupo `user_<id>` por usuário).

Funções síncronas: as views chamam direto e o consumer, via database_sync_to_async.
Os eventos carregam JSON puro; o consumer acrescenta o que depende de quem recebe (resumo da sala).
"""

import json

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.renderers import JSONRenderer

from . import services
from .models import Membership, Message, UserPresence
from .serializers import ChatUserSerializer, MessageSerializer


def user_group(user_id):
    return f'user_{user_id}'


def to_json_data(data):
    """Dados do DRF -> tipos JSON simples (o channel layer com Redis serializa em msgpack)."""
    return json.loads(JSONRenderer().render(data))


def _send(user_ids, event):
    send = async_to_sync(get_channel_layer().group_send)

    for user_id in set(user_ids):
        send(user_group(user_id), event)


def _message_event(event_type, message):
    message = Message.objects.select_related('room', 'author__profile_picture', 'reply_to__author').get(pk=message.pk)
    room = message.room
    read_ids = services.read_by_all_ids(room, [message])
    payload = to_json_data(MessageSerializer(message, context={'read_ids': read_ids}).data)

    _send(services.member_ids(room), {'type': event_type, 'room': room.id, 'message': payload})


def broadcast_message_new(message):
    _message_event('message.new', message)


def broadcast_message_update(message):
    _message_event('message.update', message)


def broadcast_typing(user, room):
    others = [user_id for user_id in services.member_ids(room) if user_id != user.pk]

    _send(others, {'type': 'typing', 'room': room.id, 'user': to_json_data(ChatUserSerializer(user).data)})


def broadcast_read(room, message_ids):
    _send(services.member_ids(room), {'type': 'read.receipt', 'room': room.id, 'message_ids': list(message_ids)})


def broadcast_room_changed(room_id, user_ids, removed_user_ids=None):
    """Avisa que a sala mudou (entrada, saída, nome, leitura...). Quem foi removido recebe `removed=True`."""
    removed = set(removed_user_ids or [])
    base = {'type': 'room.changed', 'room': room_id}

    _send([user_id for user_id in user_ids if user_id not in removed], {**base, 'removed': False})
    _send(removed, {**base, 'removed': True})


def broadcast_presence(user):
    """Avisa quem divide alguma sala com `user` que ele ficou online/offline."""
    presence = UserPresence.objects.filter(user=user).first()
    contacts = (
        Membership.objects.filter(room__memberships__user=user)
        .exclude(user=user)
        .values_list('user_id', flat=True)
        .distinct()
    )
    event = {
        'type': 'presence',
        'user_id': user.pk,
        'online': bool(presence and presence.is_online),
        'last_seen': presence.last_seen.isoformat() if presence and presence.last_seen else None,
    }

    _send(list(contacts), event)


def presence_connect(user):
    """Conta mais uma conexão; True se o usuário passou a ficar online."""
    with transaction.atomic():
        UserPresence.objects.get_or_create(user=user)
        UserPresence.objects.filter(user=user).update(connections=F('connections') + 1)
        count = UserPresence.objects.filter(user=user).values_list('connections', flat=True).first()

    return count == 1


def presence_disconnect(user):
    """Desconta uma conexão (nunca abaixo de 0); True se o usuário ficou offline (e então registra o last_seen)."""
    with transaction.atomic():
        updated = UserPresence.objects.filter(user=user, connections__gt=0).update(connections=F('connections') - 1)

        if not updated:
            return False

        presence = UserPresence.objects.get(user=user)

        if presence.connections > 0:
            return False

        UserPresence.objects.filter(pk=presence.pk).update(last_seen=timezone.now())

        return True
