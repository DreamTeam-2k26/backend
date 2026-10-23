from unittest import mock

from rest_framework.test import APIClient, APITestCase

from core.models import Membership, Project

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

        self.assertEqual(set(response.data['results'][0]), {'id', 'name', 'description', 'role', 'updated_at'})

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
