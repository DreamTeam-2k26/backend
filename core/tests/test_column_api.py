from unittest import mock

from django.db.models import ProtectedError
from rest_framework.test import APIClient, APITestCase

from core.models import Column, Membership, Project

from .helpers import PROJECTS, client_for, make_project, make_user, project_url

COLUMNS = '/api/columns/'
REORDER = f'{COLUMNS}reorder/'


def column_url(column):
    return f'{COLUMNS}{column.pk}/'


class ColumnAPITestCase(APITestCase):
    def setUp(self):
        self.owner = make_user('dono')
        self.admin = make_user('admin')
        self.member = make_user('membro')
        self.outsider = make_user('externo')
        self.project = make_project(self.owner, admins=[self.admin], members=[self.member])
        self.other_project = make_project(self.outsider, name='Outro projeto')
        self.todo, self.doing, self.done = self.project.columns.order_by('position')

    def names(self, response):
        return [item['name'] for item in response.data['results']]


class ProjectInitialColumnsTests(ColumnAPITestCase):
    def test_new_project_gets_three_columns(self):
        response = client_for(self.member).post(PROJECTS, {'name': 'Gincana'})

        project = Project.objects.get(pk=response.data['id'])
        columns = list(project.columns.order_by('position').values_list('name', 'category'))
        self.assertEqual(columns, [('A fazer', 'todo'), ('Em andamento', 'in_progress'), ('Concluído', 'done')])

    def test_editing_project_does_not_duplicate_columns(self):
        client_for(self.owner).patch(project_url(self.project), {'name': 'Novo nome'})
        client_for(self.owner).put(project_url(self.project), {'name': 'Outro nome', 'description': ''})

        self.assertEqual(self.project.columns.count(), 3)

    def test_project_is_not_saved_when_columns_fail(self):
        before = Project.objects.count()

        with (
            mock.patch.object(Column, 'create_defaults', side_effect=RuntimeError('falhou')),
            self.assertRaises(RuntimeError),
        ):
            client_for(self.member).post(PROJECTS, {'name': 'Gincana'})

        self.assertEqual(Project.objects.count(), before)
        self.assertEqual(Membership.objects.filter(user=self.member).count(), 1)


class AuthenticationTests(ColumnAPITestCase):
    def test_endpoints_require_authentication(self):
        anonymous = APIClient()
        requests = [
            ('get', COLUMNS),
            ('post', COLUMNS),
            ('post', REORDER),
            ('get', column_url(self.todo)),
            ('patch', column_url(self.todo)),
            ('delete', column_url(self.todo)),
        ]
        for method, url in requests:
            with self.subTest(method=method, url=url):
                self.assertEqual(getattr(anonymous, method)(url).status_code, 401)


class ListAndDetailTests(ColumnAPITestCase):
    def test_member_lists_columns_ordered_by_position(self):
        self.todo.position, self.done.position = 5, 0
        self.todo.save()
        self.done.save()

        response = client_for(self.member).get(COLUMNS, {'project': self.project.pk})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.names(response), ['Concluído', 'Em andamento', 'A fazer'])
        self.assertEqual(response.data['results'][0]['category'], 'done')

    def test_columns_of_other_projects_are_isolated(self):
        response = client_for(self.member).get(COLUMNS)

        self.assertEqual({item['project'] for item in response.data['results']}, {self.project.pk})

    def test_filter_by_project_of_outsider_returns_nothing(self):
        response = client_for(self.member).get(COLUMNS, {'project': self.other_project.pk})

        self.assertEqual(response.data['results'], [])

    def test_member_sees_detail(self):
        response = client_for(self.member).get(column_url(self.doing))

        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.data['name'], response.data['category']), ('Em andamento', 'in_progress'))

    def test_outsider_gets_404(self):
        self.assertEqual(client_for(self.outsider).get(column_url(self.todo)).status_code, 404)


class CreateColumnTests(ColumnAPITestCase):
    def post(self, user, **payload):
        data = {'project': self.project.pk, 'name': 'Revisão', 'category': 'in_progress', **payload}
        return client_for(user).post(COLUMNS, data)

    def test_owner_and_admin_create_column_at_the_end(self):
        for position, user in enumerate((self.owner, self.admin), start=3):
            with self.subTest(user=user.email):
                response = self.post(user)
                self.assertEqual(response.status_code, 201)
                column = Column.objects.get(pk=response.data['id'])
                self.assertEqual(
                    (column.project, column.category, column.position), (self.project, 'in_progress', position)
                )

    def test_member_cannot_create(self):
        self.assertEqual(self.post(self.member).status_code, 403)

    def test_outsider_gets_404(self):
        self.assertEqual(self.post(self.outsider).status_code, 404)

    def test_category_must_be_internal_category(self):
        response = self.post(self.owner, category='bloqueado')

        self.assertEqual(response.status_code, 400)
        self.assertIn('category', response.data)

    def test_name_is_required(self):
        response = self.post(self.owner, name='  ')

        self.assertEqual(response.status_code, 400)
        self.assertIn('name', response.data)


class UpdateColumnTests(ColumnAPITestCase):
    def test_owner_and_admin_rename(self):
        for user in (self.owner, self.admin):
            with self.subTest(user=user.email):
                response = client_for(user).patch(column_url(self.doing), {'name': f'Fazendo {user.pk}'})
                self.assertEqual(response.status_code, 200)
                self.doing.refresh_from_db()
                self.assertEqual(self.doing.name, f'Fazendo {user.pk}')

    def test_member_cannot_rename(self):
        self.assertEqual(client_for(self.member).patch(column_url(self.doing), {'name': 'X'}).status_code, 403)

    def test_outsider_gets_404(self):
        self.assertEqual(client_for(self.outsider).patch(column_url(self.doing), {'name': 'X'}).status_code, 404)

    def test_category_cannot_change(self):
        response = client_for(self.owner).patch(column_url(self.done), {'category': 'todo'})

        self.assertEqual(response.status_code, 400)
        self.assertIn('category', response.data)
        self.done.refresh_from_db()
        self.assertEqual(self.done.category, 'done')

    def test_project_cannot_change(self):
        own_other = make_project(self.owner, name='Meu outro')

        response = client_for(self.owner).patch(column_url(self.doing), {'project': own_other.pk})

        self.assertEqual(response.status_code, 400)
        self.doing.refresh_from_db()
        self.assertEqual(self.doing.project, self.project)


class ReorderColumnsTests(ColumnAPITestCase):
    def reorder(self, user, ids, project=None):
        payload = {'project': (project or self.project).pk, 'columns': ids}
        return client_for(user).post(REORDER, payload, format='json')

    def test_admin_reorders_columns(self):
        response = self.reorder(self.admin, [self.done.pk, self.todo.pk, self.doing.pk])

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['name'] for item in response.data], ['Concluído', 'A fazer', 'Em andamento'])
        listed = client_for(self.member).get(COLUMNS, {'project': self.project.pk})
        self.assertEqual(self.names(listed), ['Concluído', 'A fazer', 'Em andamento'])
        self.done.refresh_from_db()
        self.assertEqual(self.done.category, 'done')

    def test_must_list_every_column_of_project_once(self):
        cases = {
            'faltando': [self.done.pk, self.todo.pk],
            'repetida': [self.done.pk, self.todo.pk, self.doing.pk, self.todo.pk],
            'de outro projeto': [self.done.pk, self.todo.pk, self.other_project.columns.first().pk],
        }
        for label, ids in cases.items():
            with self.subTest(label):
                response = self.reorder(self.owner, ids)
                self.assertEqual(response.status_code, 400)
                self.assertIn('columns', response.data)
        self.assertEqual(list(self.project.columns.order_by('position')), [self.todo, self.doing, self.done])

    def test_member_cannot_reorder(self):
        self.assertEqual(self.reorder(self.member, [self.done.pk, self.todo.pk, self.doing.pk]).status_code, 403)

    def test_outsider_gets_404(self):
        ids = list(self.other_project.columns.values_list('pk', flat=True))

        self.assertEqual(self.reorder(self.owner, ids, project=self.other_project).status_code, 404)


class DeleteColumnTests(ColumnAPITestCase):
    def setUp(self):
        super().setUp()
        self.review = Column.objects.create(
            project=self.project, name='Revisão', category=Column.Category.IN_PROGRESS, position=3
        )

    def test_owner_deletes_extra_column(self):
        response = client_for(self.owner).delete(column_url(self.review))

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Column.objects.filter(pk=self.review.pk).exists())

    def test_last_column_of_a_category_cannot_be_deleted(self):
        response = client_for(self.owner).delete(column_url(self.done))

        self.assertEqual(response.status_code, 400)
        self.assertTrue(Column.objects.filter(pk=self.done.pk).exists())

    def test_column_with_tasks_cannot_be_deleted(self):
        with mock.patch.object(Column, 'delete', side_effect=ProtectedError('tem tarefas', set())):
            response = client_for(self.owner).delete(column_url(self.review))

        self.assertEqual(response.status_code, 400)

    def test_member_cannot_delete(self):
        self.assertEqual(client_for(self.member).delete(column_url(self.review)).status_code, 403)

    def test_outsider_gets_404(self):
        self.assertEqual(client_for(self.outsider).delete(column_url(self.review)).status_code, 404)
