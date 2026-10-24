"""Papéis e ciclo de vida da participação, cruzando várias entidades.

Cada endpoint já testa o seu 403; aqui ficam o "um único dono" ao longo de várias transferências, o que cada
papel consegue no projeto inteiro e o que acontece com tudo (tarefas, arquivos, reuniões, chat, chamadas)
quando alguém sai e volta.
"""

from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.test import APITestCase

from calls.models import Call
from chat.tests.helpers import PDF_BYTES, TempMediaMixin
from core.models import Comment, File, Folder, Meeting, Membership, Project, Task

from .helpers import client_for, make_project, make_user


def membership(project, user):
    return Membership.objects.get(project=project, user=user)


class OwnershipTests(APITestCase):
    def setUp(self):
        self.ana = make_user('ana')
        self.bia = make_user('bia')
        self.caio = make_user('caio')
        self.project = make_project(self.ana, admins=[self.bia], members=[self.caio])

    def owners(self):
        return list(Membership.objects.filter(project=self.project, role='owner').values_list('user', flat=True))

    def transfer(self, by, to):
        url = f'/api/memberships/{membership(self.project, to).pk}/'
        return client_for(by).patch(url, {'role': 'owner'}, format='json')

    def test_single_owner_through_a_chain_of_transfers(self):
        self.assertEqual(self.transfer(self.ana, self.bia).status_code, 200)
        self.assertEqual(self.owners(), [self.bia.pk])
        self.assertEqual(membership(self.project, self.ana).role, 'admin')

        # A antiga dona agora é admin: não transfere de novo, não muda papéis nem exclui o projeto.
        self.assertEqual(self.transfer(self.ana, self.caio).status_code, 403)
        demote = client_for(self.ana).patch(
            f'/api/memberships/{membership(self.project, self.caio).pk}/', {'role': 'admin'}, format='json'
        )
        self.assertEqual(demote.status_code, 403)
        self.assertEqual(client_for(self.ana).delete(f'/api/projects/{self.project.pk}/').status_code, 403)

        self.assertEqual(self.transfer(self.bia, self.caio).status_code, 200)
        self.assertEqual(self.owners(), [self.caio.pk])
        self.assertEqual(client_for(self.caio).delete(f'/api/projects/{self.project.pk}/').status_code, 204)
        self.assertFalse(Project.objects.filter(pk=self.project.pk).exists())


class RolePowersTests(APITestCase):
    """O que dono, admin e membro conseguem no projeto como um todo."""

    def setUp(self):
        self.owner = make_user('dono')
        self.admin = make_user('admin')
        self.member = make_user('membro')
        self.guest = make_user('convidado')
        self.project = make_project(self.owner, admins=[self.admin], members=[self.member])

    def attempt(self, user):
        client = client_for(user)
        project = self.project.pk
        return {
            'editar projeto': client.patch(f'/api/projects/{project}/', {'name': 'Novo nome'}).status_code,
            'criar coluna': client.post(
                '/api/columns/', {'project': project, 'name': 'Revisão', 'category': 'in_progress'}
            ).status_code,
            'convidar membro': client.post(
                '/api/invites/', {'project': project, 'kind': 'link', 'role': 'member'}
            ).status_code,
            'convidar admin': client.post(
                '/api/invites/', {'project': project, 'kind': 'link', 'role': 'admin'}
            ).status_code,
            'criar tarefa': client.post(
                '/api/tasks/', {'project': project, 'column': self.project.columns.first().pk, 'title': 'T'}
            ).status_code,
            'mudar papel': client.patch(
                f'/api/memberships/{membership(self.project, self.member).pk}/', {'role': 'member'}, format='json'
            ).status_code,
        }

    def test_powers_by_role(self):
        self.assertEqual(
            self.attempt(self.owner),
            {
                'editar projeto': 200,
                'criar coluna': 201,
                'convidar membro': 201,
                'convidar admin': 201,
                'criar tarefa': 201,
                'mudar papel': 200,
            },
        )
        self.assertEqual(
            self.attempt(self.admin),
            {
                'editar projeto': 200,
                'criar coluna': 201,
                'convidar membro': 201,
                'convidar admin': 403,
                'criar tarefa': 201,
                'mudar papel': 403,
            },
        )
        self.assertEqual(
            self.attempt(self.member),
            {
                'editar projeto': 403,
                'criar coluna': 403,
                'convidar membro': 403,
                'convidar admin': 403,
                'criar tarefa': 201,
                'mudar papel': 403,
            },
        )

    def test_removal_powers(self):
        admin2 = make_user('admin2')
        Membership.objects.create(project=self.project, user=admin2, role='admin')

        def remove(by, user):
            return client_for(by).delete(f'/api/memberships/{membership(self.project, user).pk}/').status_code

        self.assertEqual(remove(self.member, self.admin), 403)
        self.assertEqual(remove(self.admin, admin2), 403)
        self.assertEqual(remove(self.admin, self.owner), 403)
        self.assertEqual(remove(self.owner, self.owner), 400)
        self.assertEqual(remove(self.admin, self.member), 204)
        self.assertEqual(remove(self.owner, admin2), 204)


class RemovalAndReturnTests(TempMediaMixin, APITestCase):
    def setUp(self):
        self.owner = make_user('dono')
        self.member = make_user('membro')
        self.project = make_project(self.owner, members=[self.member])
        column = self.project.columns.first()
        self.task = Task.objects.create(project=self.project, column=column, title='Cartaz', assignee=self.member)
        self.comment = Comment.objects.create(task=self.task, author=self.member, body='Eu faço')
        self.folder = Folder.objects.create(project=self.project, name='Documentos', created_by=self.member)
        self.file = File.objects.create(
            project=self.project,
            folder=self.folder,
            name='edital.pdf',
            file=SimpleUploadedFile('edital.pdf', PDF_BYTES),
            size=len(PDF_BYTES),
            mime_type='application/pdf',
            uploaded_by=self.member,
        )
        self.meeting = Meeting.objects.create(
            project=self.project, title='Ensaio', starts_at=timezone.now(), url='https://meet.google.com/x'
        )
        self.call = Call.objects.create(project=self.project, title='Chamada', scheduled_for=timezone.now())
        self.room = self.project.chat_room

    def member_access(self):
        """Status de cada coisa do projeto vista pelo membro."""
        client = client_for(self.member)
        pid = self.project.pk
        return {
            'projeto': client.get(f'/api/projects/{pid}/').status_code,
            'tarefa': client.get(f'/api/tasks/{self.task.pk}/').status_code,
            'comentários': client.get(f'/api/comments/{self.comment.pk}/').status_code,
            'pasta': client.get(f'/api/folders/{self.folder.pk}/').status_code,
            'download': client.get(f'/api/files/{self.file.pk}/download/').status_code,
            'reunião': client.get(f'/api/meetings/{self.meeting.pk}/').status_code,
            'chamada': client.get(f'/api/calls/{self.call.pk}/').status_code,
            'chat': client.get(f'/api/chat/rooms/{self.room.pk}/messages/').status_code,
            'lista de tarefas': len(client.get('/api/tasks/', {'project': pid}).data['results']),
            'lista de salas': len(client.get('/api/chat/rooms/', {'project': pid}).data['results']),
        }

    def remove_member(self):
        url = f'/api/memberships/{membership(self.project, self.member).pk}/'
        self.assertEqual(client_for(self.owner).delete(url).status_code, 204)

    def test_removed_member_loses_access_to_everything(self):
        self.remove_member()

        self.assertEqual(
            self.member_access(),
            {
                'projeto': 404,
                'tarefa': 404,
                'comentários': 404,
                'pasta': 404,
                'download': 404,
                'reunião': 404,
                'chamada': 404,
                'chat': 404,
                'lista de tarefas': 0,
                'lista de salas': 0,
            },
        )

    def test_history_stays_and_assignments_are_released(self):
        self.remove_member()

        self.task.refresh_from_db()
        self.assertIsNone(self.task.assignee)
        comment = client_for(self.owner).get(f'/api/comments/{self.comment.pk}/').data
        self.assertEqual((comment['author'], comment['body']), (self.member.pk, 'Eu faço'))
        self.assertEqual(File.objects.get(pk=self.file.pk).uploaded_by, self.member)
        self.assertEqual(client_for(self.owner).get(f'/api/files/{self.file.pk}/download/').status_code, 200)

    def test_new_invite_brings_access_back(self):
        self.remove_member()
        invite = client_for(self.owner).post(
            '/api/invites/', {'project': self.project.pk, 'kind': 'direct', 'invited_user': self.member.pk}
        )

        self.assertEqual(client_for(self.member).post(f'/api/invites/{invite.data["id"]}/accept/').status_code, 200)

        access = self.member_access()
        self.assertEqual(
            access,
            {
                'projeto': 200,
                'tarefa': 200,
                'comentários': 200,
                'pasta': 200,
                'download': 200,
                'reunião': 200,
                'chamada': 200,
                'chat': 200,
                'lista de tarefas': 1,
                'lista de salas': 1,
            },
        )
        self.assertEqual(membership(self.project, self.member).role, 'member')
