"""WebSocket do chat: uma conexão por aba, em /ws/chat/?token=<access>, inscrita no grupo `user_<id>`.

Protocolo JSON.

Cliente -> servidor (todas as ações informam a sala em `room`):
    {"action": "send",   "room": 1, "body": "oi", "reply_to": 7}
    {"action": "edit",   "room": 1, "message_id": 7, "body": "novo texto"}
    {"action": "delete", "room": 1, "message_id": 7}
    {"action": "typing", "room": 1}
    {"action": "read",   "room": 1}

Servidor -> cliente (`type`): message.new, message.update, typing, presence, read.receipt,
room.updated, room.removed, error.

A identidade vem do JWT validado por core.middleware.JWTAuthMiddleware. A participação na sala é
conferida no servidor a CADA ação; o nome/id da sala enviado pelo cliente nunca é prova de acesso.
Todo acesso ao ORM passa por database_sync_to_async.
"""

import asyncio
import json
import logging

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.core.exceptions import PermissionDenied, ValidationError

from . import actions, realtime, services
from .models import Message, Room
from .serializers import RoomSerializer

logger = logging.getLogger('chat')

CLOSE_UNAUTHORIZED = 4401
MAX_FRAME_SIZE = 16 * 1024  # um texto de 2000 caracteres cabe com folga; bem acima disso é abuso
MAX_ID = 2**63  # limite de BigAutoField

ROOM_NOT_FOUND = 'Sala não encontrada.'
MESSAGE_NOT_FOUND = 'Mensagem não encontrada.'
INVALID_BODY = 'Mensagem inválida.'
UNEXPECTED = 'Algo deu errado. Tente novamente.'


class UserError(Exception):
    """Erro de entrada do cliente; o texto vira um evento `error`."""


def _to_int(value, message):
    """Id vindo do cliente: inteiro (ou string numérica) entre 1 e 2**63 - 1."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise UserError(message)

    try:
        number = int(value)
    except ValueError as error:
        raise UserError(message) from error

    if not 0 < number < MAX_ID:
        raise UserError(message)

    return number


def _body(value):
    """Texto vindo do cliente: precisa ser string (o resto da validação fica em services)."""
    if value is not None and not isinstance(value, str):
        raise UserError(INVALID_BODY)

    return value


def _error_text(error):
    if isinstance(error, ValidationError):
        return ' '.join(error.messages)

    if isinstance(error, Message.DoesNotExist):
        return MESSAGE_NOT_FOUND

    return str(error) or 'Ação não permitida.'


# ---- operações síncronas (rodam em thread via database_sync_to_async) ----


def _member_room(user, room_id):
    """Sala se o usuário participa dela; senão UserError (sem distinguir 'não existe' de 'não participa')."""
    room = services.get_room_for(user, room_id)

    if room is None:
        raise UserError(ROOM_NOT_FOUND)

    return room


def _do_send(user, room_id, body, reply_to_id):
    actions.send_message(user, _member_room(user, room_id), body=body, reply_to_id=reply_to_id)


def _do_edit(user, room_id, message_id, body):
    actions.edit_message(user, _member_room(user, room_id), message_id, body)


def _do_delete(user, room_id, message_id):
    actions.delete_message(user, _member_room(user, room_id), message_id)


def _do_typing(user, room_id):
    actions.notify_typing(user, _member_room(user, room_id))


def _do_read(user, room_id):
    actions.mark_read(user, _member_room(user, room_id))


def _room_payload(user, room_id):
    """Resumo da sala para `user` (com o contador dele), ou None se a sala sumiu / ele saiu dela."""
    room = Room.objects.filter(pk=room_id).first()

    if room is None:
        return None

    try:
        summary = services.room_summary(user, room)
    except PermissionDenied:
        return None

    return realtime.to_json_data(RoomSerializer(summary).data)


class ChatConsumer(AsyncWebsocketConsumer):
    # Valores iniciais para a limpeza funcionar mesmo se a conexão cair antes de connect() terminar.
    user = None
    user_group = None
    counted = False

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            # Rede de segurança: se um handler falhar (ex.: o cliente sumiu no meio de um envio), o Channels
            # encerra o consumer SEM chamar disconnect(). Sem isto o contador de presença vazaria para sempre.
            # shield: a limpeza termina mesmo que a tarefa esteja sendo cancelada (ex.: desligamento do servidor).
            await asyncio.shield(self._cleanup())

    async def connect(self):
        self.user = self.scope.get('user')

        if self.user is None or not self.user.is_authenticated:
            # Aceita e fecha com código próprio para o cliente distinguir "não autorizado" de "rede caiu"
            # (antes do accept o navegador só enxerga 1006) e não ficar tentando reconectar em loop.
            await self.accept()
            await self.close(code=CLOSE_UNAUTHORIZED)

            return

        self.user_group = realtime.user_group(self.user.pk)
        await self.channel_layer.group_add(self.user_group, self.channel_name)
        await self.accept()

        became_online = await database_sync_to_async(realtime.presence_connect)(self.user)
        self.counted = True  # já contado: a limpeza precisa descontar mesmo se o broadcast falhar

        if became_online:
            await database_sync_to_async(realtime.broadcast_presence)(self.user)

    async def disconnect(self, close_code):
        await self._cleanup()

    async def _cleanup(self):
        """Sai do grupo e desconta a presença. Idempotente: pode rodar em disconnect() e no fim do consumer."""
        if self.user_group:
            group, self.user_group = self.user_group, None
            await self.channel_layer.group_discard(group, self.channel_name)

        if self.counted:
            self.counted = False

            if await database_sync_to_async(realtime.presence_disconnect)(self.user):
                await database_sync_to_async(realtime.broadcast_presence)(self.user)

    async def receive(self, text_data=None, bytes_data=None):
        if self.user_group is None:  # conexão anônima já em encerramento
            return

        if not text_data or len(text_data) > MAX_FRAME_SIZE:
            await self.send_error('Mensagem inválida.')

            return

        try:
            data = json.loads(text_data)
        except ValueError:
            logger.warning('WS: JSON inválido de %s', self.user.pk)
            await self.send_error('Mensagem inválida.')

            return

        if not isinstance(data, dict):
            await self.send_error('Mensagem inválida.')

            return

        action = data.get('action')
        handler = {
            'send': self._send_action,
            'edit': self._edit_action,
            'delete': self._delete_action,
            'typing': self._typing_action,
            'read': self._read_action,
        }.get(action)

        if handler is None:
            logger.warning('WS: ação desconhecida %r de %s', action, self.user.pk)
            await self.send_error('Ação desconhecida.')

            return

        try:
            room_id = _to_int(data.get('room'), ROOM_NOT_FOUND)
            await handler(room_id, data)
        except UserError as error:
            await self.send_error(str(error), action=action)
        except (ValidationError, PermissionDenied, Message.DoesNotExist) as error:
            await self.send_error(_error_text(error), action=action)
        except Exception:
            # Erro inesperado não derruba a conexão: registra e avisa o usuário.
            logger.exception('WS: erro ao processar %r de %s', action, self.user.pk)
            await self.send_error(UNEXPECTED, action=action)

    async def send_error(self, detail, action=None):
        await self.send_json({'type': 'error', 'action': action, 'detail': detail})

    async def send_json(self, payload):
        try:
            await self.send(text_data=json.dumps(payload, ensure_ascii=False))
        except OSError:
            # ASGI 2.4: enviar a um cliente que já desconectou levanta OSError (uvicorn: ClientDisconnected).
            # Não é falha do servidor: o websocket.disconnect já está a caminho e fará a limpeza normal.
            logger.debug('WS: cliente %s desconectou durante o envio de %r', getattr(self.user, 'pk', None), payload.get('type'))

    # ---- ações do cliente ----

    async def _send_action(self, room_id, data):
        reply_to = data.get('reply_to')
        reply_to = _to_int(reply_to, 'Mensagem respondida inválida.') if reply_to not in (None, '') else None

        await database_sync_to_async(_do_send)(self.user, room_id, _body(data.get('body')), reply_to)

    async def _edit_action(self, room_id, data):
        message_id = _to_int(data.get('message_id'), MESSAGE_NOT_FOUND)

        await database_sync_to_async(_do_edit)(self.user, room_id, message_id, _body(data.get('body')))

    async def _delete_action(self, room_id, data):
        message_id = _to_int(data.get('message_id'), MESSAGE_NOT_FOUND)

        await database_sync_to_async(_do_delete)(self.user, room_id, message_id)

    async def _typing_action(self, room_id, data):
        await database_sync_to_async(_do_typing)(self.user, room_id)

    async def _read_action(self, room_id, data):
        await database_sync_to_async(_do_read)(self.user, room_id)

    # ---- eventos do channel layer (um método por `type`, com '.' trocado por '_') ----

    async def _send_room(self, room_id, event_type='room.updated'):
        """Envia o resumo da sala para este usuário; se ele não participa mais, avisa que a sala saiu da lista."""
        summary = await database_sync_to_async(_room_payload)(self.user, room_id)

        if summary is None:
            await self.send_json({'type': 'room.removed', 'room': room_id})
        else:
            await self.send_json({'type': event_type, 'room': room_id, 'summary': summary})

    async def message_new(self, event):
        await self.send_json({'type': 'message.new', 'room': event['room'], 'message': event['message']})
        await self._send_room(event['room'])

    async def message_update(self, event):
        await self.send_json({'type': 'message.update', 'room': event['room'], 'message': event['message']})
        await self._send_room(event['room'])

    async def typing(self, event):
        await self.send_json({'type': 'typing', 'room': event['room'], 'user': event['user']})

    async def presence(self, event):
        await self.send_json({
            'type': 'presence',
            'user_id': event['user_id'],
            'online': event['online'],
            'last_seen': event['last_seen'],
        })

    async def read_receipt(self, event):
        await self.send_json({'type': 'read.receipt', 'room': event['room'], 'message_ids': event['message_ids']})

    async def room_changed(self, event):
        if event['removed']:
            await self.send_json({'type': 'room.removed', 'room': event['room']})

            return

        await self._send_room(event['room'])
