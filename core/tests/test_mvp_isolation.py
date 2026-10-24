"""Isolamento entre projetos em TODAS as APIs de projeto, num lugar só.

As tasks testam cada entidade com um projeto alheio; aqui ficam (1) a varredura de 401/404 em todas as rotas,
(2) a pessoa que participa dos DOIS projetos tentando misturar ids — a validação tem que olhar o projeto,
não só a participação — e (3) uma trava para rota nova de projeto não ficar fora desta varredura.
"""

from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from app.urls import router
from calls.models import Call
from chat.tests.helpers import PDF_BYTES, TempMediaMixin
from core.models import (
    BrainstormReply,
    BrainstormTopic,
    ChecklistItem,
    Comment,
    File,
    Folder,
    Invite,
    Meeting,
    Membership,
    SupportLink,
    Task,
)

from .helpers import client_for, make_project, make_user

# Rotas do router principal que NÃO são de projeto (cadastro/permissões globais).
GLOBAL_PREFIXES = {'users', 'groups', 'permissions'}


class ProjectWorld:
    """Um projeto com uma instância de cada entidade."""

    def __init__(self, owner, name, members=()):
        self.project = make_project(owner, name=name, members=members)
        self.todo, self.doing, self.done = self.project.columns.order_by('position')
        self.task = Task.objects.create(project=self.project, column=self.todo, title=f'Tarefa {name}')
        self.item = ChecklistItem.objects.create(task=self.task, text='Item')
        self.comment = Comment.objects.create(task=self.task, author=owner, body='Comentário')
        self.link = SupportLink.objects.create(project=self.project, title='Edital', url='https://exemplo.com')
        self.meeting = Meeting.objects.create(
            project=self.project, title='Reunião', starts_at=timezone.now(), url='https://meet.google.com/x'
        )
        self.folder = Folder.objects.create(project=self.project, name='Documentos')
        self.file = File.objects.create(
            project=self.project,
            name='edital.pdf',
            file=SimpleUploadedFile('edital.pdf', PDF_BYTES),
            size=len(PDF_BYTES),
            mime_type='application/pdf',
        )
        self.invite = Invite.objects.create(project=self.project, kind=Invite.Kind.LINK, token=f'token-{name}')
        self.call = Call.objects.create(project=self.project, title='Chamada', scheduled_for=timezone.now())
        self.room = self.project.chat_room
        self.membership = Membership.objects.get(project=self.project, user=owner)
        self.topic = BrainstormTopic.objects.create(project=self.project, author=owner, title='Tema', context='x')
        self.reply = BrainstormReply.objects.create(topic=self.topic, author=owner, content='Ideia')

    def detail_urls(self):
        """(url, métodos que alteram) de cada recurso do projeto."""
        return [
            (f'/api/projects/{self.project.pk}/', ('patch', 'delete')),
            (f'/api/projects/{self.project.pk}/dashboard/', ()),
            (f'/api/memberships/{self.membership.pk}/', ('patch', 'delete')),
            (f'/api/invites/{self.invite.pk}/', ()),
            (f'/api/columns/{self.todo.pk}/', ('patch', 'delete')),
            (f'/api/tasks/{self.task.pk}/', ('patch', 'delete')),
            (f'/api/tasks/{self.task.pk}/files/', ()),
            (f'/api/checklist-items/{self.item.pk}/', ('patch', 'delete')),
            (f'/api/comments/{self.comment.pk}/', ('patch', 'delete')),
            (f'/api/support-links/{self.link.pk}/', ('patch', 'delete')),
            (f'/api/meetings/{self.meeting.pk}/', ('patch', 'delete')),
            (f'/api/folders/{self.folder.pk}/', ('patch', 'delete')),
            (f'/api/files/{self.file.pk}/', ('patch', 'delete')),
            (f'/api/files/{self.file.pk}/download/', ()),
            (f'/api/calls/{self.call.pk}/', ('patch', 'delete')),
            (f'/api/chat/rooms/{self.room.pk}/', ()),
            (f'/api/chat/rooms/{self.room.pk}/messages/', ()),
            (f'/api/brainstorm-topics/{self.topic.pk}/', ('patch', 'delete')),
            (f'/api/brainstorm-topics/{self.topic.pk}/versions/', ()),
            (f'/api/brainstorm-replies/{self.reply.pk}/', ('patch', 'delete')),
        ]

    def list_requests(self):
        """(url, filtro) das listagens filtradas por este projeto."""
        project = self.project.pk
        return [
            ('/api/memberships/', {'project': project}),
            ('/api/invites/', {'project': project}),
            ('/api/columns/', {'project': project}),
            ('/api/tasks/', {'project': project}),
            ('/api/checklist-items/', {'task': self.task.pk}),
            ('/api/comments/', {'task': self.task.pk}),
            ('/api/support-links/', {'project': project}),
            ('/api/meetings/', {'project': project}),
            ('/api/folders/', {'project': project}),
            ('/api/files/', {'project': project}),
            ('/api/calls/', {'project': project}),
            ('/api/chat/rooms/', {'project': project}),
            ('/api/brainstorm-topics/', {'project': project}),
            ('/api/brainstorm-replies/', {'topic': self.topic.pk}),
        ]


class IsolationTestCase(TempMediaMixin, APITestCase):
    def setUp(self):
        self.owner = make_user('dono')
        self.both = make_user('dos-dois')
        self.outsider = make_user('externo')
        self.a = ProjectWorld(self.owner, 'A', members=[self.both])
        self.b = ProjectWorld(self.outsider, 'B', members=[self.both])


class AccessMatrixTests(IsolationTestCase):
    def test_every_project_route_requires_jwt(self):
        anonymous = APIClient()
        for url, _ in self.a.detail_urls() + self.a.list_requests():
            with self.subTest(url=url):
                self.assertEqual(anonymous.get(url).status_code, 401)

    def test_outsider_gets_404_on_every_resource_and_changes_nothing(self):
        client = client_for(self.outsider)
        for url, write_methods in self.a.detail_urls():
            for method in ('get', *write_methods):
                with self.subTest(method=method, url=url):
                    self.assertEqual(getattr(client, method)(url, {}, format='json').status_code, 404)
        self.assertEqual(Task.objects.filter(project=self.a.project).count(), 1)
        self.assertEqual(Membership.objects.filter(project=self.a.project).count(), 2)

    def test_outsider_lists_nothing_of_the_project(self):
        client = client_for(self.outsider)
        for url, params in self.a.list_requests():
            with self.subTest(url=url):
                response = client.get(url, params)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data['results'], [])

    def test_every_project_route_is_covered_by_this_matrix(self):
        # Rota nova de projeto no router? Adicione em ProjectWorld.detail_urls/list_requests.
        covered = {url.split('/')[2] for url, _ in self.a.detail_urls() + self.a.list_requests()}
        registered = {prefix for prefix, *_ in router.registry} - GLOBAL_PREFIXES

        self.assertEqual(registered - covered, set())


class MixedIdsTests(IsolationTestCase):
    """Quem participa de A e de B não consegue ligar coisas de um projeto às do outro."""

    def setUp(self):
        super().setUp()
        self.client_both = client_for(self.both)

    def assert_rejected(self, response, field):
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn(field, response.data)

    def test_task_with_column_of_the_other_project(self):
        response = self.client_both.post(
            '/api/tasks/', {'project': self.a.project.pk, 'column': self.b.todo.pk, 'title': 'Mistura'}
        )

        self.assert_rejected(response, 'column')

    def test_task_assigned_to_someone_only_in_the_other_project(self):
        response = self.client_both.patch(f'/api/tasks/{self.a.task.pk}/', {'assignee': self.outsider.pk})

        self.assert_rejected(response, 'assignee')

    def test_move_task_to_column_of_the_other_project(self):
        response = self.client_both.post(
            f'/api/tasks/{self.a.task.pk}/move/', {'column_id': self.b.doing.pk, 'position': 0}, format='json'
        )

        self.assert_rejected(response, 'column_id')
        self.a.task.refresh_from_db()
        self.assertEqual(self.a.task.column, self.a.todo)

    def test_reorder_columns_with_ids_of_the_other_project(self):
        ids = [self.a.todo.pk, self.a.doing.pk, self.b.done.pk]

        response = self.client_both.post(
            '/api/columns/reorder/', {'project': self.a.project.pk, 'columns': ids}, format='json'
        )

        # "both" é membro comum em A: o papel barra antes (403); como dono de B, a mistura daria 400.
        self.assertEqual(response.status_code, 403)
        mixed = client_for(self.owner).post(
            '/api/columns/reorder/', {'project': self.a.project.pk, 'columns': ids}, format='json'
        )
        self.assert_rejected(mixed, 'columns')

    def test_attach_file_of_the_other_project_to_task(self):
        response = self.client_both.post(f'/api/tasks/{self.a.task.pk}/files/', {'file': self.b.file.pk}, format='json')

        self.assert_rejected(response, 'file')

    def test_support_link_pointing_to_task_of_the_other_project(self):
        response = self.client_both.post(
            '/api/support-links/',
            {'project': self.a.project.pk, 'task': self.b.task.pk, 'title': 'X', 'url': 'https://exemplo.com'},
        )

        self.assert_rejected(response, 'task')

    def test_folder_inside_folder_of_the_other_project(self):
        response = self.client_both.post(
            '/api/folders/', {'project': self.a.project.pk, 'parent': self.b.folder.pk, 'name': 'X'}, format='json'
        )

        self.assert_rejected(response, 'parent')

    def test_file_uploaded_into_folder_of_the_other_project(self):
        response = self.client_both.post(
            '/api/files/',
            {
                'project': self.a.project.pk,
                'folder': self.b.folder.pk,
                'file': SimpleUploadedFile('ata.pdf', PDF_BYTES, content_type='application/pdf'),
            },
            format='multipart',
        )

        self.assert_rejected(response, 'folder')

    def test_resources_cannot_be_moved_to_the_other_project(self):
        # Reunião só é editada por quem agendou (ou dono/admin): agendada por "both" para chegar na regra do projeto.
        Meeting.objects.filter(pk=self.a.meeting.pk).update(created_by=self.both)
        resources = [self.a.task, self.a.link, self.a.meeting, self.a.folder, self.a.file]
        urls = ['tasks', 'support-links', 'meetings', 'folders', 'files']
        for resource, url in zip(resources, urls, strict=True):
            with self.subTest(url=url):
                response = self.client_both.patch(
                    f'/api/{url}/{resource.pk}/', {'project': self.b.project.pk}, format='json'
                )
                self.assert_rejected(response, 'project')
                resource.refresh_from_db()
                self.assertEqual(resource.project, self.a.project)
