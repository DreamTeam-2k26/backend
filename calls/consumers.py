"""WebSockets das chamadas (autenticados pelo JWT em ?token=, como o chat).

/ws/calls/ (CallNotifyConsumer): avisos de chamada dos meus projetos. Só servidor -> cliente:
    {"type": "call.changed", "call": {...}, "removed": false}

/ws/calls/<id>/ (CallRoomConsumer): a sala da chamada e a sinalização WebRTC.
Servidor -> cliente (`type`):
    welcome      {you, peers: [{user, name, avatar, state}], ice_servers, max_participants, transcription}
    peer.joined  {peer}            peer.left {user}            peer.state {user, state}
    signal       {from, data}      (oferta/resposta SDP ou candidato ICE, repassados sem interpretar)
    call.ended   {}                replaced {}  (entrou de outra aba)           error {message}
    transcription.changed {enabled, by}      transcript.segment {segment: {id, user, name, text, offset_ms, ...}}
Cliente -> servidor (`action`):
    {"action": "signal", "to": <id>, "data": {...}}
    {"action": "state",  "state": {"mic": true, "camera": false, "screen": false, "hand": false}}
    {"action": "transcript", "text": "...", "client_id": "...", "duration_ms": 1800}  (só com a transcrição ligada)
    {"action": "leave"}

A identidade vem do token; o cliente nunca informa "quem sou eu". Participação no projeto e chamada
em andamento são conferidas na entrada; a sinalização só chega a quem está na sala.
"""

import json
import logging
import time

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.core.exceptions import PermissionDenied, ValidationError
from rest_framework.renderers import JSONRenderer

from chat.serializers.user import display_name

from . import conf, realtime, services, transcription
from .serializers import TranscriptSegmentSerializer

logger = logging.getLogger('calls')

CLOSE_UNAUTHORIZED = 4401
CLOSE_DENIED = 4403
CLOSE_ENDED = 4410
CLOSE_REPLACED = 4409
MAX_FRAME_SIZE = 64 * 1024  # uma oferta SDP com vários vídeos passa de 10 KB; acima disso é abuso
MAX_ID = 2**63
SEGMENT_WINDOW = 10.0  # segundos
SEGMENT_LIMIT = 20  # falas aceitas por conexão nessa janela: fala humana não chega perto, abuso sim


def _error_text(error):
    if isinstance(error, ValidationError):
        return ' '.join(error.messages)

    return str(error) or 'Ação não permitida.'


class CallNotifyConsumer(AsyncWebsocketConsumer):
    """Entrega ao navegador os avisos de chamada dos projetos do usuário."""

    async def connect(self):
        user = self.scope.get('user')

        if user is None or not user.is_authenticated:
            await self.close(code=CLOSE_UNAUTHORIZED)

            return

        self.group = realtime.user_group(user.pk)
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        group = getattr(self, 'group', None)

        if group:
            await self.channel_layer.group_discard(group, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        """Canal só de avisos: qualquer coisa enviada pelo cliente é ignorada."""

    async def call_changed(self, event):
        await self.send(
            text_data=json.dumps({'type': 'call.changed', 'call': event['call'], 'removed': event['removed']})
        )


class CallRoomConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        user = self.scope.get('user')

        if user is None or not user.is_authenticated:
            await self.close(code=CLOSE_UNAUTHORIZED)

            return

        self.user = user
        self.call_id = int(self.scope['url_route']['kwargs']['call_id'])
        self.joined = False
        self.segment_times = []
        self.group = realtime.room_group(self.call_id)

        if not 0 < self.call_id < MAX_ID:
            await self.close(code=CLOSE_DENIED)

            return

        await self.accept()

        try:
            participant, replaced = await database_sync_to_async(services.join)(self.call_id, user, self.channel_name)
        except (PermissionDenied, ValidationError) as error:
            await self._send({'type': 'error', 'message': _error_text(error)})
            await self.close(code=CLOSE_DENIED)

            return

        self.joined = True

        if replaced:
            await self.channel_layer.send(replaced, {'type': 'replaced'})

        await self.channel_layer.group_add(self.group, self.channel_name)
        peers = await self._peers(exclude=user.pk)
        await self._send({
            'type': 'welcome',
            'you': user.pk,
            'peers': peers,
            'ice_servers': conf.ice_servers(),
            'max_participants': conf.max_participants(),
            'transcription': await database_sync_to_async(transcription.is_active)(self.call_id),
        })
        me = await self._peer(participant)
        await self.channel_layer.group_send(
            self.group, {'type': 'peer.joined', 'peer': me, 'sender': self.channel_name}
        )
        await self._announce_change()

    async def disconnect(self, code):
        await self._depart()

    async def _depart(self):
        """Sai da chamada uma única vez (ação "leave" ou queda da conexão) e avisa os outros."""
        if not getattr(self, 'joined', False):
            return

        self.joined = False
        await self.channel_layer.group_discard(self.group, self.channel_name)
        left, ended = await database_sync_to_async(self._leave)()

        if left and not ended:
            await self.channel_layer.group_send(self.group, {'type': 'peer.left', 'user': self.user.pk})

        if left:
            await self._announce_change()

    async def receive(self, text_data=None, bytes_data=None):
        if not self.joined or text_data is None or len(text_data) > MAX_FRAME_SIZE:
            return

        try:
            payload = json.loads(text_data)
        except ValueError:
            return

        if not isinstance(payload, dict):
            return

        action = payload.get('action')

        if action == 'signal':
            await self._relay_signal(payload)
        elif action == 'state':
            await self._update_state(payload.get('state'))
        elif action == 'transcript':
            await self._add_segment(payload)
        elif action == 'leave':
            await self._depart()
            await self.close()

    async def _relay_signal(self, payload):
        target, data = payload.get('to'), payload.get('data')

        if isinstance(target, bool) or not isinstance(target, int) or not isinstance(data, dict):
            return

        await self.channel_layer.group_send(
            self.group, {'type': 'signal', 'from': self.user.pk, 'to': target, 'data': data}
        )

    async def _update_state(self, raw):
        state = services.sanitize_state(raw)
        await database_sync_to_async(services.set_state)(self.call_id, self.user, self.channel_name, state)
        await self.channel_layer.group_send(
            self.group, {'type': 'peer.state', 'user': self.user.pk, 'state': state, 'sender': self.channel_name}
        )

    async def _add_segment(self, payload):
        if not self._within_segment_limit():
            return

        segment = await database_sync_to_async(self._save_segment)(payload)

        if segment is not None:
            await self.channel_layer.group_send(self.group, {'type': 'transcript.segment', 'segment': segment})

    def _within_segment_limit(self):
        now = time.monotonic()
        self.segment_times = [moment for moment in self.segment_times if now - moment < SEGMENT_WINDOW]

        if len(self.segment_times) >= SEGMENT_LIMIT:
            return False

        self.segment_times.append(now)

        return True

    def _save_segment(self, payload):
        segment = transcription.add_segment(
            self.call_id, self.user, payload.get('text'), payload.get('client_id'), payload.get('duration_ms')
        )

        return None if segment is None else json.loads(JSONRenderer().render(TranscriptSegmentSerializer(segment).data))

    # Eventos do grupo -> cliente

    async def signal(self, event):
        if event['to'] == self.user.pk:
            await self._send({'type': 'signal', 'from': event['from'], 'data': event['data']})

    async def peer_joined(self, event):
        if event['sender'] != self.channel_name:
            await self._send({'type': 'peer.joined', 'peer': event['peer']})

    async def peer_left(self, event):
        if event['user'] != self.user.pk:
            await self._send({'type': 'peer.left', 'user': event['user']})

    async def peer_state(self, event):
        if event['sender'] != self.channel_name:
            await self._send({'type': 'peer.state', 'user': event['user'], 'state': event['state']})

    async def transcription_changed(self, event):
        await self._send({'type': 'transcription.changed', 'enabled': event['enabled'], 'by': event['by']})

    async def transcript_segment(self, event):
        await self._send({'type': 'transcript.segment', 'segment': event['segment']})

    async def call_ended(self, event):
        await self._send({'type': 'call.ended'})
        self.joined = False
        await self.close(code=CLOSE_ENDED)

    async def replaced(self, event):
        """Outra aba do mesmo usuário entrou: esta sai sem mexer no estado da chamada."""
        await self._send({'type': 'replaced'})
        self.joined = False
        await self.channel_layer.group_discard(self.group, self.channel_name)
        await self.close(code=CLOSE_REPLACED)

    # Auxiliares

    async def _send(self, payload):
        await self.send(text_data=json.dumps(payload))

    def _leave(self):
        return services.leave(self.call_id, self.user, self.channel_name)

    @database_sync_to_async
    def _peers(self, exclude):
        return [
            self._serialize(item) for item in services.active_participants_for(self.call_id) if item.user_id != exclude
        ]

    @database_sync_to_async
    def _peer(self, participant):
        return self._serialize(services.participant_with_user(participant.pk))

    @database_sync_to_async
    def _announce_change(self):
        from .models import Call  # noqa: PLC0415

        call = Call.objects.select_related('project', 'created_by').get(pk=self.call_id)
        realtime.broadcast_call_changed(call)

    @staticmethod
    def _serialize(participant):
        user = participant.user
        image = user.profile_picture

        return {
            'user': user.pk,
            'name': display_name(user),
            'avatar': image.url if image else None,
            'state': participant.state or {},
        }
