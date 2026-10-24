"""Chat em tempo real acompanhando a participação no PROJETO, pela API pública (não pelos services do chat).

test_consumer.py já cobre remover alguém direto da sala; aqui a remoção e a volta acontecem como no app:
DELETE /api/memberships/{id}/ e aceite de convite, e o WebSocket precisa refletir isso.
"""

from channels.db import database_sync_to_async

from core.models import Invite
from core.models import Membership as ProjectMembership
from core.tests.helpers import client_for, make_project

from .test_consumer import ConsumerBase


class ProjectChatAccessTests(ConsumerBase):
    def setUp(self):
        super().setUp()
        self.project = make_project(self.ana, name='Feira', members=[self.bia])
        self.project_room = self.project.chat_room

    @database_sync_to_async
    def remove_bia_from_project(self):
        membership = ProjectMembership.objects.get(project=self.project, user=self.bia)
        return client_for(self.ana).delete(f'/api/memberships/{membership.pk}/').status_code

    @database_sync_to_async
    def invite_bia_back(self):
        invite = Invite.objects.create(project=self.project, kind=Invite.Kind.DIRECT, invited_user=self.bia)
        return client_for(self.bia).post(f'/api/invites/{invite.pk}/accept/').status_code

    @database_sync_to_async
    def bia_rest_status(self):
        return client_for(self.bia).get(f'/api/chat/rooms/{self.project_room.pk}/messages/').status_code

    async def test_removed_from_project_loses_live_chat_and_returns_with_invite(self):
        ana = await self.connect(self.ana)
        bia = await self.connect(self.bia)
        await self.drain(ana)
        await self.drain(bia)

        # Removida do projeto pela API: recebe a ordem de largar a sala e perde envio, recebimento e REST.
        self.assertEqual(await self.remove_bia_from_project(), 204)
        removed = self.of_type(await self.drain(bia), 'room.removed')
        self.assertEqual([event['room'] for event in removed], [self.project_room.pk])

        await self.send(bia, action='send', room=self.project_room.pk, body='ainda aqui?')
        self.assertEqual(self.of_type(await self.drain(bia), 'error')[0]['detail'], 'Sala não encontrada.')
        await self.send(ana, action='send', room=self.project_room.pk, body='só para quem ficou')
        await self.drain(ana)
        self.assertEqual(self.of_type(await self.drain(bia), 'message.new'), [])
        self.assertEqual(await self.bia_rest_status(), 404)

        # Convidada de novo e aceitando, volta a conversar na mesma sala, com o histórico.
        self.assertEqual(await self.invite_bia_back(), 200)
        await self.drain(bia)
        await self.send(bia, action='send', room=self.project_room.pk, body='voltei')
        delivered = self.of_type(await self.drain(ana), 'message.new')
        self.assertEqual([event['message']['body'] for event in delivered], ['voltei'])
        self.assertEqual(await self.bia_rest_status(), 200)
        self.assertEqual([m.body for m in await self.messages()], ['só para quem ficou', 'voltei'])
