from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient, APITestCase

from chat.tests.helpers import JPEG_BYTES, TempMediaMixin
from core.models import Column, Membership, Project, Task
from uploader.models import Image

from .helpers import PROJECTS, client_for, make_project, make_user, project_url


class ProjectAPITestCase(APITestCase):
    def setUp(self):
        self.owner = make_user('dono')
        self.admin = make_user('admin')
        self.member = make_user('membro')
        self.outsider = make_user('externo')
        self.project = make_project(self.owner, admins=[self.admin], members=[self.member])


class AuthenticationTests(ProjectAPITestCase):
    def test_endpoints_require_authentication(self):
        anonymous = APIClient()
        requests = [
            ('get', PROJECTS),
            ('post', PROJECTS),
            ('get', project_url(self.project)),
            ('patch', project_url(self.project)),
            ('delete', project_url(self.project)),
        ]
        for method, url in requests:
            with self.subTest(method=method, url=url):
                self.assertEqual(getattr(anonymous, method)(url).status_code, 401)


class CreateProjectTests(ProjectAPITestCase):
    def test_creator_becomes_owner(self):
        response = client_for(self.outsider).post(PROJECTS, {'name': 'Gincana', 'description': 'Turma 3INFO'})

        self.assertEqual(response.status_code, 201)
        project = Project.objects.get(pk=response.data['id'])
        self.assertEqual(project.created_by, self.outsider)
        self.assertEqual(response.data['role'], 'owner')
        membership = Membership.objects.get(project=project)
        self.assertEqual((membership.user, membership.role), (self.outsider, Membership.Role.OWNER))

    def test_description_is_optional(self):
        response = client_for(self.outsider).post(PROJECTS, {'name': 'Gincana'})

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['description'], '')

    def test_name_is_required(self):
        for payload in ({}, {'name': ''}, {'name': '   '}):
            with self.subTest(payload=payload):
                response = client_for(self.outsider).post(PROJECTS, payload)
                self.assertEqual(response.status_code, 400)
                self.assertIn('name', response.data)

    def test_created_by_cannot_be_forged(self):
        response = client_for(self.outsider).post(PROJECTS, {'name': 'Gincana', 'created_by': self.owner.pk})

        self.assertEqual(Project.objects.get(pk=response.data['id']).created_by, self.outsider)

    def test_project_is_not_saved_when_owner_membership_fails(self):
        before = Project.objects.count()

        with (
            mock.patch.object(Membership.objects, 'create', side_effect=RuntimeError('falhou')),
            self.assertRaises(RuntimeError),
        ):
            client_for(self.outsider).post(PROJECTS, {'name': 'Gincana'})

        self.assertEqual(Project.objects.count(), before)


class ListAndDetailTests(ProjectAPITestCase):
    def test_list_only_projects_user_participates_in(self):
        make_project(self.outsider, name='Projeto do externo')

        response = client_for(self.member).get(PROJECTS)

        self.assertEqual(response.status_code, 200)
        self.assertEqual([p['id'] for p in response.data['results']], [self.project.pk])

    def test_list_shows_role_of_requesting_user(self):
        for user, role in ((self.owner, 'owner'), (self.admin, 'admin'), (self.member, 'member')):
            with self.subTest(role=role):
                response = client_for(user).get(PROJECTS)
                self.assertEqual(response.data['results'][0]['role'], role)

    def test_list_returns_only_card_fields(self):
        response = client_for(self.member).get(PROJECTS)

        self.assertEqual(
            set(response.data['results'][0]),
            {'id', 'name', 'description', 'total_memberships', 'memberships', 'progress', 'role', 'updated_at'},
        )

    def test_list_returns_participants_with_user_id_and_picture(self):
        response = client_for(self.member).get(PROJECTS)

        card = response.data['results'][0]
        self.assertEqual(card['total_memberships'], 3)
        self.assertEqual(
            card['memberships'],
            [
                {'id': self.owner.pk, 'profile_picture': None},
                {'id': self.admin.pk, 'profile_picture': None},
                {'id': self.member.pk, 'profile_picture': None},
            ],
        )

    def test_detail_returns_all_fields(self):
        response = client_for(self.member).get(project_url(self.project))

        self.assertEqual(
            set(response.data),
            {'id', 'name', 'description', 'created_by', 'created_at', 'updated_at', 'role'},
        )

    def test_member_sees_detail(self):
        response = client_for(self.member).get(project_url(self.project))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['name'], self.project.name)
        self.assertEqual(response.data['role'], 'member')

    def test_outsider_gets_404(self):
        response = client_for(self.outsider).get(project_url(self.project))

        self.assertEqual(response.status_code, 404)


class UpdateProjectTests(ProjectAPITestCase):
    def test_owner_and_admin_can_edit(self):
        for user in (self.owner, self.admin):
            with self.subTest(user=user.email):
                response = client_for(user).patch(project_url(self.project), {'name': f'Novo {user.pk}'})
                self.assertEqual(response.status_code, 200)
                self.project.refresh_from_db()
                self.assertEqual(self.project.name, f'Novo {user.pk}')

    def test_member_cannot_edit(self):
        response = client_for(self.member).patch(project_url(self.project), {'name': 'Hack'})

        self.assertEqual(response.status_code, 403)
        self.project.refresh_from_db()
        self.assertEqual(self.project.name, 'Feira de Ciências')

    def test_outsider_gets_404(self):
        response = client_for(self.outsider).patch(project_url(self.project), {'name': 'Hack'})

        self.assertEqual(response.status_code, 404)

    def test_edit_cannot_change_created_by(self):
        client_for(self.owner).patch(project_url(self.project), {'created_by': self.member.pk})
        self.project.refresh_from_db()

        self.assertEqual(self.project.created_by, self.owner)

    def test_admin_edit_keeps_original_creator(self):
        client_for(self.admin).patch(project_url(self.project), {'name': 'Novo nome'})
        self.project.refresh_from_db()

        self.assertEqual(self.project.created_by, self.owner)

    def test_put_replaces_name_and_description(self):
        response = client_for(self.admin).put(project_url(self.project), {'name': 'Gincana', 'description': 'Nova'})

        self.assertEqual(response.status_code, 200)
        self.project.refresh_from_db()
        self.assertEqual((self.project.name, self.project.description), ('Gincana', 'Nova'))


class DeleteProjectTests(ProjectAPITestCase):
    def test_owner_can_delete(self):
        response = client_for(self.owner).delete(project_url(self.project))

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Project.objects.filter(pk=self.project.pk).exists())
        self.assertFalse(Membership.objects.filter(project_id=self.project.pk).exists())

    def test_admin_and_member_cannot_delete(self):
        for user in (self.admin, self.member):
            with self.subTest(user=user.email):
                response = client_for(user).delete(project_url(self.project))
                self.assertEqual(response.status_code, 403)
                self.assertTrue(Project.objects.filter(pk=self.project.pk).exists())

    def test_outsider_gets_404(self):
        response = client_for(self.outsider).delete(project_url(self.project))

        self.assertEqual(response.status_code, 404)
        self.assertTrue(Project.objects.filter(pk=self.project.pk).exists())

    def test_owner_is_whoever_holds_owner_role_not_created_by(self):
        Project.objects.filter(pk=self.project.pk).update(created_by=self.admin)

        response = client_for(self.admin).delete(project_url(self.project))

        self.assertEqual(response.status_code, 403)


class ProjectCardParticipantsTests(TempMediaMixin, APITestCase):
    def setUp(self):
        self.owner = make_user('dono-foto')
        self.owner.profile_picture = Image.objects.create(
            file=SimpleUploadedFile('foto.jpg', JPEG_BYTES, content_type='image/jpeg'), uploaded_by=self.owner
        )
        self.owner.save()

    def test_participant_picture_is_returned_as_url(self):
        # Projetos de outra pessoa antes: o id da membership deixa de coincidir com o id do usuário.
        other = make_user('outra')
        make_project(other)
        make_project(other, name='Mais um')
        project = make_project(self.owner)
        self.assertNotEqual(project.memberships.get().pk, self.owner.pk)

        card = client_for(self.owner).get(PROJECTS).data['results'][0]

        self.assertEqual(
            card['memberships'], [{'id': self.owner.pk, 'profile_picture': self.owner.profile_picture.url}]
        )

    def test_list_query_count_does_not_grow_with_projects(self):
        make_project(self.owner, members=[make_user('m1')])
        with CaptureQueriesContext(connection) as one_project:
            client_for(self.owner).get(PROJECTS)

        for index in range(4):
            make_project(
                self.owner, name=f'Projeto {index}', members=[make_user(f'm-{index}-a'), make_user(f'm-{index}-b')]
            )
        with CaptureQueriesContext(connection) as five_projects:
            client_for(self.owner).get(PROJECTS)

        self.assertEqual(len(five_projects), len(one_project))


class ProjectProgressTests(APITestCase):
    def setUp(self):
        self.owner = make_user('dono-progresso')
        self.project = make_project(self.owner)
        self.todo, self.doing, self.done = self.project.columns.order_by('position')

    def add_tasks(self, column, amount, project=None):
        for index in range(amount):
            Task.objects.create(project=project or self.project, column=column, title=f'{column.name} {index}')

    def progress(self):
        cards = client_for(self.owner).get(PROJECTS).data['results']
        return next(card['progress'] for card in cards if card['id'] == self.project.pk)

    def test_project_without_tasks_is_zero(self):
        self.assertEqual(self.progress(), 0)

    def test_percentage_of_tasks_in_done_columns(self):
        self.add_tasks(self.todo, 2)
        self.add_tasks(self.doing, 1)
        self.add_tasks(self.done, 1)

        self.assertEqual(self.progress(), 25)

    def test_percentage_is_rounded(self):
        self.add_tasks(self.todo, 2)
        self.add_tasks(self.done, 1)

        self.assertEqual(self.progress(), 33)

    def test_custom_done_column_counts_as_done(self):
        extra_done = Column.objects.create(project=self.project, name='Entregue', category='done', position=3)
        self.add_tasks(self.todo, 1)
        self.add_tasks(extra_done, 1)

        self.assertEqual(self.progress(), 50)

    def test_all_done_is_one_hundred(self):
        self.add_tasks(self.done, 3)

        self.assertEqual(self.progress(), 100)

    def test_tasks_of_other_projects_do_not_count(self):
        other = make_project(self.owner, name='Outro')
        other_todo, _, other_done = other.columns.order_by('position')
        self.add_tasks(other_done, 5, project=other)
        self.add_tasks(self.todo, 1)

        self.assertEqual(self.progress(), 0)

    def test_many_participants_do_not_inflate_counts(self):
        for index in range(3):
            Membership.objects.create(project=self.project, user=make_user(f'part-{index}'))
        self.add_tasks(self.todo, 1)
        self.add_tasks(self.done, 1)

        self.assertEqual(self.progress(), 50)
