import asyncio
import contextlib
import functools
import json
from inspect import iscoroutinefunction

from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import TransactionTestCase, override_settings

from app.asgi import application
from calls import services
from calls.models import Call

from .helpers import access_token, client_for, setup_team

IN_MEMORY = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}


@override_settings(CHANNEL_LAYERS=IN_MEMORY)
class RoomBase(TransactionTestCase):
    """Os testes passam pelo `app.asgi.application` real: roteamento + JWT + consumers."""

    def setUp(self):
        self.project, self.owner, self.admin, self.member, self.outsider = setup_team()
        self.call = services.start_now(self.project, self.owner, 'Reunião')
        self.open_connections = []

    def _setup_and_call(self, result, debug=False):
        method = getattr(self, self._testMethodName)

        if iscoroutinefunction(method):

            @functools.wraps(method)
            async def with_cleanup():
                try:
                    await method()
                finally:
                    for communicator in self.open_connections:
                        with contextlib.suppress(Exception, asyncio.CancelledError):
                            await communicator.disconnect()

            setattr(self, self._testMethodName, with_cleanup)

        super()._setup_and_call(result, debug)

    async def connect(self, user, path=None, token=True):
        path = path or f'/ws/calls/{self.call.pk}/'
        query = f'?token={access_token(user)}' if token else ''
        communicator = WebsocketCommunicator(application, f'{path}{query}')
        connected, code = await communicator.connect()
        self.open_connections.append(communicator)

        return communicator, connected, code

    async def enter(self, user):
        communicator, connected, _ = await self.connect(user)
        self.assertTrue(connected)
        welcome = await communicator.receive_json_from()
        self.assertEqual(welcome['type'], 'welcome')

        return communicator, welcome

    async def drain(self, communicator, expected_type):
        """Lê mensagens até achar o tipo esperado (ignora avisos de contagem etc.)."""
        for _ in range(6):
            message = await communicator.receive_json_from()

            if message['type'] == expected_type:
                return message

        self.fail(f'Não chegou {expected_type}')


class RoomConsumerTests(RoomBase):
    async def test_sem_token_e_rejeitado(self):
        communicator, connected, code = await self.connect(self.owner, token=False)
        self.assertFalse(connected)
        self.assertEqual(code, 4401)

    async def test_fora_do_projeto_e_negado(self):
        communicator, connected, _ = await self.connect(self.outsider)
        self.assertTrue(connected)
        message = await communicator.receive_json_from()
        self.assertEqual(message['type'], 'error')
        self.assertEqual((await communicator.receive_output())['type'], 'websocket.close')

    async def test_nao_entra_em_chamada_encerrada(self):
        await database_sync_to_async(services.end_call)(self.call)
        communicator, _, _ = await self.connect(self.owner)
        message = await communicator.receive_json_from()
        self.assertEqual(message['type'], 'error')
        self.assertIn('não está em andamento', message['message'])

    async def test_entrada_traz_welcome_com_ice_e_quem_ja_esta(self):
        first, welcome = await self.enter(self.owner)
        self.assertEqual(welcome['you'], self.owner.pk)
        self.assertEqual(welcome['peers'], [])
        self.assertTrue(welcome['ice_servers'])
        second, welcome2 = await self.enter(self.member)
        self.assertEqual([peer['user'] for peer in welcome2['peers']], [self.owner.pk])
        joined = await self.drain(first, 'peer.joined')
        self.assertEqual(joined['peer']['user'], self.member.pk)
        self.assertEqual(joined['peer']['state'], {})

    async def test_sinal_chega_so_ao_destinatario(self):
        a, _ = await self.enter(self.owner)
        b, _ = await self.enter(self.admin)
        c, _ = await self.enter(self.member)
        await a.send_json_to({'action': 'signal', 'to': self.admin.pk, 'data': {'sdp': 'oferta'}})
        signal = await self.drain(b, 'signal')
        self.assertEqual(signal, {'type': 'signal', 'from': self.owner.pk, 'data': {'sdp': 'oferta'}})
        # c e o próprio remetente não recebem nada de sinal
        await a.send_json_to({'action': 'state', 'state': {'mic': True}})
        for other in (c,):
            seen = []
            for _ in range(5):
                if await other.receive_nothing(timeout=0.2):
                    break
                seen.append((await other.receive_json_from())['type'])
            self.assertNotIn('signal', seen)

    async def test_sinal_invalido_e_ignorado(self):
        a, _ = await self.enter(self.owner)
        b, _ = await self.enter(self.admin)
        for bad in (
            {'action': 'signal', 'to': 'x', 'data': {}},
            {'action': 'signal', 'to': self.admin.pk, 'data': 'texto'},
        ):
            await a.send_json_to(bad)
        await a.send_to(text_data='não é json')
        await a.send_to(text_data=json.dumps([1, 2]))
        await a.send_json_to({'action': 'signal', 'to': self.admin.pk, 'data': {'ok': 1}})
        signal = await self.drain(b, 'signal')
        self.assertEqual(signal['data'], {'ok': 1})

    async def test_estado_e_repassado_e_guardado(self):
        a, _ = await self.enter(self.owner)
        b, _ = await self.enter(self.admin)
        await a.send_json_to({'action': 'state', 'state': {'mic': True, 'camera': 1, 'hand': True, 'x': 'y'}})
        state = await self.drain(b, 'peer.state')
        self.assertEqual(state['user'], self.owner.pk)
        self.assertEqual(
            state['state'], {'mic': True, 'camera': True, 'screen': False, 'hand': True, 'screen_stream': ''}
        )
        _, welcome = await self.enter(self.member)
        owner_peer = next(peer for peer in welcome['peers'] if peer['user'] == self.owner.pk)
        self.assertTrue(owner_peer['state']['hand'])

    async def test_saida_avisa_e_ultima_pessoa_encerra(self):
        a, _ = await self.enter(self.owner)
        b, _ = await self.enter(self.admin)
        await b.send_json_to({'action': 'leave'})
        left = await self.drain(a, 'peer.left')
        self.assertEqual(left['user'], self.admin.pk)
        await a.send_json_to({'action': 'leave'})
        await a.receive_output()
        call = None

        for _ in range(20):
            call = await database_sync_to_async(Call.objects.get)(pk=self.call.pk)

            if call.status == Call.Status.ENDED:
                break

            await asyncio.sleep(0.05)

        self.assertEqual(call.status, Call.Status.ENDED)

    async def test_encerrar_pela_api_derruba_todos(self):
        a, _ = await self.enter(self.owner)
        b, _ = await self.enter(self.member)
        response = await database_sync_to_async(client_for(self.owner).post)(f'/api/calls/{self.call.pk}/end/')
        self.assertEqual(response.status_code, 200)

        for communicator in (a, b):
            ended = await self.drain(communicator, 'call.ended')
            self.assertEqual(ended, {'type': 'call.ended'})

    async def test_outra_aba_assume_o_lugar(self):
        old, _ = await self.enter(self.member)
        new, _ = await self.enter(self.member)
        replaced = await self.drain(old, 'replaced')
        self.assertEqual(replaced['type'], 'replaced')
        # a nova continua na chamada
        others, _ = await self.enter(self.owner)
        joined = await self.drain(new, 'peer.joined')
        self.assertEqual(joined['peer']['user'], self.owner.pk)


class NotifyConsumerTests(RoomBase):
    async def test_aviso_de_chamada_iniciada_so_para_o_projeto(self):
        await database_sync_to_async(services.end_call)(self.call)
        member, connected, _ = await self.connect(self.member, path='/ws/calls/')
        self.assertTrue(connected)
        outsider, connected, _ = await self.connect(self.outsider, path='/ws/calls/')
        self.assertTrue(connected)
        response = await database_sync_to_async(client_for(self.owner).post)(
            '/api/calls/', {'project': self.project.pk, 'title': 'Nova'}, format='json'
        )
        self.assertEqual(response.status_code, 201)
        message = await member.receive_json_from()
        self.assertEqual(message['type'], 'call.changed')
        self.assertEqual(message['call']['title'], 'Nova')
        self.assertEqual(message['call']['status'], 'live')
        self.assertFalse(message['removed'])
        self.assertTrue(await outsider.receive_nothing(timeout=0.3))

    async def test_sem_token_e_rejeitado(self):
        communicator, connected, code = await self.connect(self.owner, path='/ws/calls/', token=False)
        self.assertFalse(connected)
        self.assertEqual(code, 4401)
