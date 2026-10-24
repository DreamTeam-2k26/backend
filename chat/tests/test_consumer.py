import functools
import json
from inspect import iscoroutinefunction
from unittest import mock

from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import TransactionTestCase, override_settings

from app.asgi import application
from chat import services
from chat.consumers import ChatConsumer
from chat.models import Message, UserPresence

from .helpers import access_token, make_room, make_user

IN_MEMORY = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}


@override_settings(CHANNEL_LAYERS=IN_MEMORY)
class ConsumerBase(TransactionTestCase):
    """Todos os testes passam pelo `app.asgi.application` real: roteamento + middleware JWT + consumer."""

    def setUp(self):
        self.ana = make_user('ana')
        self.bia = make_user('bia')
        self.caio = make_user('caio')
        self.room = make_room(self.ana, self.bia, name='Projeto')
        self.open_connections = []

    def _setup_and_call(self, result, debug=False):
        # O Django converte métodos de teste async com async_to_sync mas não roda asyncTearDown:
        # embrulha o teste para sempre fechar as conexões abertas (e descontar a presença) no mesmo loop.
        method = getattr(self, self._testMethodName)

        if iscoroutinefunction(method):

            @functools.wraps(method)
            async def with_cleanup():
                try:
                    await method()
                finally:
                    await self._close_all()

            setattr(self, self._testMethodName, with_cleanup)

        super()._setup_and_call(result, debug)

    async def _close_all(self):
        for communicator in self.open_connections:
            try:
                await communicator.disconnect()
            except Exception:  # noqa: BLE001, S110 - já encerrada pelo servidor
                pass

        self.open_connections.clear()

    async def connect(self, user=None, token=None, path='/ws/chat/'):
        """Abre uma conexão autenticada como `user` (ou com o `token` literal)."""
        if token is None and user is not None:
            token = access_token(user)

        query = f'?token={token}' if token else ''
        communicator = WebsocketCommunicator(application, f'{path}{query}')
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        self.open_connections.append(communicator)

        return communicator

    async def send(self, communicator, **payload):
        await communicator.send_to(text_data=json.dumps(payload))

    async def drain(self, communicator, timeout=0.3):
        """Todos os eventos até ficar `timeout` s em silêncio."""
        events = []

        while not await communicator.receive_nothing(timeout=timeout):
            events.append(json.loads(await communicator.receive_from()))

        return events

    @staticmethod
    def of_type(events, event_type):
        return [event for event in events if event['type'] == event_type]

    @database_sync_to_async
    def messages(self):
        return list(Message.objects.order_by('id'))

    @database_sync_to_async
    def message_count(self):
        return Message.objects.count()

    @database_sync_to_async
    def connections(self, user):
        presence = UserPresence.objects.filter(user=user).first()

        return presence.connections if presence else 0

    @database_sync_to_async
    def last_seen(self, user):
        return UserPresence.objects.get(user=user).last_seen


class ConnectionAuthTests(ConsumerBase):
    async def test_anonymous_connection_is_closed_with_4401(self):
        communicator = await self.connect()  # sem token
        output = await communicator.receive_output(timeout=1)
        self.assertEqual(output['type'], 'websocket.close')
        self.assertEqual(output['code'], 4401)

    async def test_invalid_token_is_closed_with_4401(self):
        communicator = await self.connect(token='isto.nao.eh-um-jwt')
        output = await communicator.receive_output(timeout=1)
        self.assertEqual((output['type'], output['code']), ('websocket.close', 4401))

    async def test_refresh_token_is_not_accepted_as_access_token(self):
        from rest_framework_simplejwt.tokens import RefreshToken  # noqa: PLC0415

        refresh = await database_sync_to_async(lambda: str(RefreshToken.for_user(self.ana)))()
        communicator = await self.connect(token=refresh)
        output = await communicator.receive_output(timeout=1)
        self.assertEqual(output['code'], 4401)

    async def test_inactive_user_token_is_rejected(self):
        token = access_token(self.caio)
        await database_sync_to_async(lambda: self.caio.__class__.objects.filter(pk=self.caio.pk).update(is_active=False))()
        communicator = await self.connect(token=token)
        output = await communicator.receive_output(timeout=1)
        self.assertEqual(output['code'], 4401)

    async def test_anonymous_cannot_act_even_before_close(self):
        communicator = await self.connect()
        await self.send(communicator, action='send', room=self.room.pk, body='anonimo')  # antes de ler o close
        output = await communicator.receive_output(timeout=1)
        self.assertEqual((output['type'], output['code']), ('websocket.close', 4401))
        self.assertEqual(await self.message_count(), 0)

    async def test_user_id_in_query_is_not_identity(self):
        # Só o JWT identifica; um user_id forjado na query string não faz nada.
        communicator = await self.connect(path='/ws/chat/?user_id=%d' % self.ana.pk)
        output = await communicator.receive_output(timeout=1)
        self.assertEqual(output['code'], 4401)

    async def test_valid_token_connects_and_tracks_presence(self):
        await self.connect(self.ana)
        self.assertEqual(await self.connections(self.ana), 1)

    async def test_unknown_path_is_not_routed(self):
        communicator = WebsocketCommunicator(application, f'/ws/chat/1/?token={access_token(self.ana)}')
        with self.assertRaises(Exception):  # noqa: B017, PT011 - URLRouter levanta ValueError (sem rota)
            await communicator.connect()


class PresenceTests(ConsumerBase):
    async def test_counts_one_connection_per_tab_and_goes_offline_on_last(self):
        first = await self.connect(self.ana)
        second = await self.connect(self.ana)
        self.assertEqual(await self.connections(self.ana), 2)
        await first.disconnect()
        self.assertEqual(await self.connections(self.ana), 1)
        self.assertIsNone(await self.last_seen(self.ana))
        await second.disconnect()
        self.assertEqual(await self.connections(self.ana), 0)
        self.assertIsNotNone(await self.last_seen(self.ana))
        self.open_connections.clear()

    async def test_room_mates_are_told_when_user_goes_online_and_offline(self):
        bia = await self.connect(self.bia)
        await self.drain(bia)
        ana = await self.connect(self.ana)
        events = self.of_type(await self.drain(bia), 'presence')
        self.assertEqual([(e['user_id'], e['online']) for e in events], [(self.ana.pk, True)])
        await ana.disconnect()
        events = self.of_type(await self.drain(bia), 'presence')
        self.assertEqual([(e['user_id'], e['online']) for e in events], [(self.ana.pk, False)])
        self.assertIsNotNone(events[0]['last_seen'])

    async def test_second_tab_does_not_repeat_online_event(self):
        bia = await self.connect(self.bia)
        await self.drain(bia)
        await self.connect(self.ana)
        await self.connect(self.ana)
        online = [e for e in self.of_type(await self.drain(bia), 'presence') if e['online']]
        self.assertEqual(len(online), 1)

    async def test_strangers_are_not_told(self):
        caio = await self.connect(self.caio)
        await self.drain(caio)
        await self.connect(self.ana)
        self.assertEqual(self.of_type(await self.drain(caio), 'presence'), [])

    async def test_presence_is_released_even_if_a_handler_crashes(self):
        # Regressão: se um handler levanta, o Channels encerra o consumer sem chamar disconnect().
        # Sem a rede de segurança em __call__, o contador ficava preso em 1 para sempre.
        communicator = await self.connect(self.ana)
        self.assertEqual(await self.connections(self.ana), 1)

        with mock.patch.object(ChatConsumer, 'receive', side_effect=RuntimeError('boom')):
            await self.send(communicator, action='typing', room=self.room.pk)

            with self.assertRaises(RuntimeError):
                await communicator.wait(timeout=2)

        self.assertEqual(await self.connections(self.ana), 0)
        self.assertIsNotNone(await self.last_seen(self.ana))
        self.open_connections.clear()

    async def test_send_to_a_client_that_already_left_does_not_kill_the_consumer(self):
        # Regressão: enviar a um cliente que desconectou levanta OSError (uvicorn: ClientDisconnected).
        bia = await self.connect(self.bia)
        ana = await self.connect(self.ana)
        await self.drain(bia)
        await self.drain(ana)
        original = ChatConsumer.send

        async def flaky_send(consumer, *args, **kwargs):
            if consumer.user.pk == self.bia.pk and kwargs.get('text_data'):
                raise OSError('client disconnected')

            return await original(consumer, *args, **kwargs)

        with mock.patch.object(ChatConsumer, 'send', flaky_send):
            await self.send(ana, action='send', room=self.room.pk, body='oi')
            await self.drain(ana)

        # bia segue conectada e contada; o fechamento normal desconta.
        self.assertEqual(await self.connections(self.bia), 1)
        await bia.disconnect()
        self.assertEqual(await self.connections(self.bia), 0)
        self.open_connections.remove(bia)


class SendTests(ConsumerBase):
    async def test_message_reaches_all_members_and_is_persisted(self):
        ana = await self.connect(self.ana)
        bia = await self.connect(self.bia)
        await self.drain(ana)
        await self.drain(bia)
        await self.send(ana, action='send', room=self.room.pk, body='oi pessoal')
        for communicator in (ana, bia):
            received = self.of_type(await self.drain(communicator), 'message.new')
            self.assertEqual(len(received), 1)  # sem duplicatas
            message = received[0]['message']
            self.assertEqual(message['body'], 'oi pessoal')
            self.assertEqual(message['author']['id'], self.ana.pk)
            self.assertEqual(message['room'], self.room.pk)
            self.assertEqual(received[0]['room'], self.room.pk)
        stored = await self.messages()
        self.assertEqual([(m.body, m.author_id, m.room_id) for m in stored], [('oi pessoal', self.ana.pk, self.room.pk)])

    async def test_each_recipient_gets_its_own_unread_counter(self):
        ana = await self.connect(self.ana)
        bia = await self.connect(self.bia)
        await self.drain(ana)
        await self.drain(bia)
        await self.send(ana, action='send', room=self.room.pk, body='um')
        ana_events = await self.drain(ana)
        bia_events = await self.drain(bia)
        self.assertEqual(self.of_type(ana_events, 'room.updated')[0]['summary']['unread'], 0)
        self.assertEqual(self.of_type(bia_events, 'room.updated')[0]['summary']['unread'], 1)

    async def test_both_tabs_of_the_same_user_receive_it(self):
        tab1 = await self.connect(self.ana)
        tab2 = await self.connect(self.ana)
        sender = await self.connect(self.bia)
        for communicator in (tab1, tab2, sender):
            await self.drain(communicator)
        await self.send(sender, action='send', room=self.room.pk, body='ola')
        for communicator in (tab1, tab2):
            self.assertEqual(len(self.of_type(await self.drain(communicator), 'message.new')), 1)

    async def test_non_member_cannot_send_and_nothing_leaks(self):
        ana = await self.connect(self.ana)
        caio = await self.connect(self.caio)
        await self.drain(ana)
        await self.drain(caio)
        await self.send(caio, action='send', room=self.room.pk, body='intruso')
        events = await self.drain(caio)
        self.assertEqual([e['type'] for e in events], ['error'])
        self.assertEqual(events[0]['detail'], 'Sala não encontrada.')
        self.assertEqual(await self.drain(ana), [])
        self.assertEqual(await self.message_count(), 0)

    async def test_nonexistent_room_gives_the_same_error_as_forbidden_room(self):
        caio = await self.connect(self.caio)
        await self.send(caio, action='send', room=self.room.pk, body='x')
        await self.send(caio, action='send', room=999999, body='x')
        errors = self.of_type(await self.drain(caio), 'error')
        self.assertEqual(len(errors), 2)
        self.assertEqual(errors[0]['detail'], errors[1]['detail'])

    async def test_non_member_receives_no_events_of_the_room(self):
        ana = await self.connect(self.ana)
        caio = await self.connect(self.caio)
        await self.drain(ana)
        await self.drain(caio)
        await self.send(ana, action='send', room=self.room.pk, body='so para membros')
        await self.drain(ana)
        self.assertEqual(await self.drain(caio), [])

    async def test_author_is_the_authenticated_user_not_a_payload_field(self):
        caio = await self.connect(self.caio)
        ana = await self.connect(self.ana)
        await self.drain(ana)
        await self.send(ana, action='send', room=self.room.pk, body='eu', author=self.bia.pk, user_id=self.bia.pk)
        await self.drain(ana)
        self.assertEqual((await self.messages())[0].author_id, self.ana.pk)
        await self.drain(caio)

    async def test_removed_member_stops_sending_and_receiving(self):
        ana = await self.connect(self.ana)
        bia = await self.connect(self.bia)
        await self.drain(ana)
        await self.drain(bia)
        await database_sync_to_async(
            lambda: services.remove_member(self.ana, self.room, self.bia)
        )()
        await self.send(bia, action='send', room=self.room.pk, body='ainda aqui?')
        self.assertEqual(self.of_type(await self.drain(bia), 'error')[0]['detail'], 'Sala não encontrada.')
        await self.send(ana, action='send', room=self.room.pk, body='so a ana')
        await self.drain(ana)
        self.assertEqual(self.of_type(await self.drain(bia), 'message.new'), [])
        self.assertEqual([m.body for m in await self.messages()], ['so a ana'])

    async def test_reply_to(self):
        first = await database_sync_to_async(
            lambda: services.send_message(self.bia, self.room, body='primeira')
        )()
        ana = await self.connect(self.ana)
        await self.drain(ana)
        await self.send(ana, action='send', room=self.room.pk, body='resposta', reply_to=str(first.pk))
        message = self.of_type(await self.drain(ana), 'message.new')[0]['message']
        self.assertEqual(message['reply_to']['id'], first.pk)
        self.assertEqual(message['reply_to']['body'], 'primeira')

    async def test_invalid_payloads_get_errors_and_keep_the_connection(self):
        ana = await self.connect(self.ana)
        await self.drain(ana)
        bad = [
            dict(action='send', room=self.room.pk, body=''),
            dict(action='send', room=self.room.pk, body='   '),
            dict(action='send', room=self.room.pk, body='x' * 2001),
            dict(action='send', room=self.room.pk, body=123),
            dict(action='send', room=self.room.pk, body=['a']),
            dict(action='send', room=self.room.pk),
            dict(action='send', room=self.room.pk, body='x', reply_to='abc'),
            dict(action='send', room=self.room.pk, body='x', reply_to=999999),
            dict(action='send', body='sem sala'),
            dict(action='send', room='abc', body='x'),
            dict(action='send', room=True, body='x'),
            dict(action='send', room=-1, body='x'),
            dict(action='send', room=2**70, body='x'),
            dict(action='voar', room=self.room.pk),
            dict(room=self.room.pk, body='sem action'),
        ]
        for payload in bad:
            await self.send(ana, **payload)
        await ana.send_to(text_data='isso não é json')
        await ana.send_to(text_data='[1, 2]')
        await ana.send_to(text_data='"texto"')
        await ana.send_to(text_data='x' * (16 * 1024 + 1))
        errors = self.of_type(await self.drain(ana), 'error')
        self.assertEqual(len(errors), len(bad) + 4)
        self.assertEqual(await self.message_count(), 0)
        # a conexão continua útil
        await self.send(ana, action='send', room=self.room.pk, body='ainda funciona')
        self.assertEqual(len(self.of_type(await self.drain(ana), 'message.new')), 1)

    async def test_unicode_and_markup_are_stored_verbatim(self):
        ana = await self.connect(self.ana)
        await self.drain(ana)
        text = '<script>alert("oi")</script> ação ✓ 😀'
        await self.send(ana, action='send', room=self.room.pk, body=text)
        message = self.of_type(await self.drain(ana), 'message.new')[0]['message']
        self.assertEqual(message['body'], text)  # escapar é papel da interface


class EditDeleteTests(ConsumerBase):
    async def seed(self, author):
        return await database_sync_to_async(lambda: services.send_message(author, self.room, body='original'))()

    async def test_author_edits_and_everyone_sees_it(self):
        message = await self.seed(self.ana)
        ana = await self.connect(self.ana)
        bia = await self.connect(self.bia)
        await self.drain(ana)
        await self.drain(bia)
        await self.send(ana, action='edit', room=self.room.pk, message_id=message.pk, body='editada')
        for communicator in (ana, bia):
            updates = self.of_type(await self.drain(communicator), 'message.update')
            self.assertEqual(updates[0]['message']['body'], 'editada')
            self.assertIsNotNone(updates[0]['message']['edited_at'])

    async def test_other_member_cannot_edit_or_delete(self):
        message = await self.seed(self.ana)
        bia = await self.connect(self.bia)
        await self.drain(bia)
        await self.send(bia, action='edit', room=self.room.pk, message_id=message.pk, body='sequestrada')
        await self.send(bia, action='delete', room=self.room.pk, message_id=message.pk)
        errors = self.of_type(await self.drain(bia), 'error')
        self.assertEqual(len(errors), 2)
        stored = (await self.messages())[0]
        self.assertEqual((stored.body, stored.is_deleted), ('original', False))

    async def test_cannot_use_another_room_as_a_shortcut(self):
        # Bia é membro da sala A mas manda editar uma mensagem da sala B (onde ela não está).
        other = await database_sync_to_async(lambda: make_room(self.ana, name='Outra'))()
        foreign = await database_sync_to_async(lambda: services.send_message(self.ana, other, body='b'))()
        bia = await self.connect(self.bia)
        await self.drain(bia)
        await self.send(bia, action='delete', room=self.room.pk, message_id=foreign.pk)
        await self.send(bia, action='delete', room=other.pk, message_id=foreign.pk)
        self.assertEqual(len(self.of_type(await self.drain(bia), 'error')), 2)
        self.assertFalse((await self.messages())[0].is_deleted)

    async def test_author_deletes_and_everyone_sees_tombstone(self):
        message = await self.seed(self.ana)
        ana = await self.connect(self.ana)
        bia = await self.connect(self.bia)
        await self.drain(ana)
        await self.drain(bia)
        await self.send(ana, action='delete', room=self.room.pk, message_id=message.pk)
        for communicator in (ana, bia):
            payload = self.of_type(await self.drain(communicator), 'message.update')[0]['message']
            self.assertTrue(payload['is_deleted'])
            self.assertIsNone(payload['body'])

    async def test_bad_message_ids(self):
        ana = await self.connect(self.ana)
        await self.drain(ana)
        for message_id in (None, 'abc', 999999, -5, True):
            await self.send(ana, action='edit', room=self.room.pk, message_id=message_id, body='x')
        self.assertEqual(len(self.of_type(await self.drain(ana), 'error')), 5)


class TypingAndReadTests(ConsumerBase):
    async def test_typing_goes_to_others_only(self):
        ana = await self.connect(self.ana)
        bia = await self.connect(self.bia)
        caio = await self.connect(self.caio)
        for communicator in (ana, bia, caio):
            await self.drain(communicator)
        await self.send(ana, action='typing', room=self.room.pk)
        typing = self.of_type(await self.drain(bia), 'typing')
        self.assertEqual((typing[0]['room'], typing[0]['user']['id']), (self.room.pk, self.ana.pk))
        self.assertEqual(await self.drain(ana), [])
        self.assertEqual(await self.drain(caio), [])

    async def test_non_member_typing_is_rejected(self):
        bia = await self.connect(self.bia)
        caio = await self.connect(self.caio)
        await self.drain(bia)
        await self.drain(caio)
        await self.send(caio, action='typing', room=self.room.pk)
        self.assertEqual(self.of_type(await self.drain(caio), 'error')[0]['detail'], 'Sala não encontrada.')
        self.assertEqual(await self.drain(bia), [])

    async def test_read_receipt_reaches_the_author(self):
        message = await database_sync_to_async(lambda: services.send_message(self.ana, self.room, body='leia'))()
        ana = await self.connect(self.ana)
        bia = await self.connect(self.bia)
        await self.drain(ana)
        await self.drain(bia)
        await self.send(bia, action='read', room=self.room.pk)
        ana_events = await self.drain(ana)
        self.assertEqual(self.of_type(ana_events, 'read.receipt')[0]['message_ids'], [message.pk])
        bia_events = await self.drain(bia)
        updated = self.of_type(bia_events, 'room.updated')
        self.assertEqual(updated[-1]['summary']['unread'], 0)

    async def test_non_member_cannot_mark_read(self):
        caio = await self.connect(self.caio)
        await self.drain(caio)
        await self.send(caio, action='read', room=self.room.pk)
        self.assertEqual(len(self.of_type(await self.drain(caio), 'error')), 1)


class RoomLifecycleEventTests(ConsumerBase):
    async def test_user_removed_from_room_is_told_to_drop_it(self):
        ana = await self.connect(self.ana)
        bia = await self.connect(self.bia)
        await self.drain(ana)
        await self.drain(bia)
        from chat import actions  # noqa: PLC0415

        await database_sync_to_async(lambda: actions.remove_member(self.ana, self.room, self.bia))()
        self.assertEqual(self.of_type(await self.drain(bia), 'room.removed')[0]['room'], self.room.pk)
        updated = self.of_type(await self.drain(ana), 'room.updated')
        self.assertEqual(updated[-1]['summary']['member_count'], 1)

    async def test_added_member_receives_the_new_room(self):
        from chat import actions  # noqa: PLC0415

        caio = await self.connect(self.caio)
        await self.drain(caio)
        await database_sync_to_async(lambda: actions.add_members(self.ana, self.room, [self.caio]))()
        updated = self.of_type(await self.drain(caio), 'room.updated')
        self.assertEqual(updated[0]['summary']['name'], 'Projeto')
        # e a partir daí passa a receber mensagens
        ana = await self.connect(self.ana)
        await self.drain(ana)
        await self.send(ana, action='send', room=self.room.pk, body='bem-vindo')
        self.assertEqual(len(self.of_type(await self.drain(caio), 'message.new')), 1)

    async def test_user_leaving_is_told_to_drop_the_room(self):
        from chat import actions  # noqa: PLC0415

        bia = await self.connect(self.bia)
        await self.drain(bia)
        await database_sync_to_async(lambda: actions.leave_room(self.bia, self.room))()
        self.assertEqual(self.of_type(await self.drain(bia), 'room.removed')[0]['room'], self.room.pk)
