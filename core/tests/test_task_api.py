from datetime import timedelta

from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from core.models import Column, Membership, Task

from .helpers import client_for, make_project, make_user

TASKS = '/api/tasks/'


def task_url(task):
    return f'{TASKS}{task.pk}/'


class TaskAPITestCase(APITestCase):
    def setUp(self):
        self.owner = make_user('dono')
        self.admin = make_user('admin')
        self.member = make_user('membro')
        self.outsider = make_user('externo')
        self.project = make_project(self.owner, admins=[self.admin], members=[self.member])
        self.other_project = make_project(self.outsider, name='Outro projeto')
        self.todo, self.doing, self.done = self.project.columns.order_by('position')
        self.foreign_column = self.other_project.columns.first()

    def make_task(self, title='Cartaz', column=None, **extra):
        column = column or self.todo
        return Task.objects.create(project=column.project, column=column, title=title, **extra)

    def today(self):
        return timezone.localdate()

    def titles(self, response):
        return [item['title'] for item in response.data['results']]


class AuthenticationTests(TaskAPITestCase):
    def test_endpoints_require_authentication(self):
        anonymous = APIClient()
        task = self.make_task()
        for method, url in (
            ('get', TASKS),
            ('post', TASKS),
            ('get', task_url(task)),
            ('patch', task_url(task)),
            ('delete', task_url(task)),
        ):
            with self.subTest(method=method, url=url):
                self.assertEqual(getattr(anonymous, method)(url).status_code, 401)


class CreateTaskTests(TaskAPITestCase):
    def post(self, user=None, **payload):
        data = {'project': self.project.pk, 'column': self.todo.pk, 'title': 'Montar cartaz', **payload}
        return client_for(user or self.member).post(TASKS, data)

    def test_any_member_creates_task(self):
        for user in (self.owner, self.admin, self.member):
            with self.subTest(user=user.email):
                response = self.post(user)
                self.assertEqual(response.status_code, 201)
                task = Task.objects.get(pk=response.data['id'])
                self.assertEqual((task.created_by, task.project, task.column), (user, self.project, self.todo))

    def test_full_payload(self):
        due = self.today() + timedelta(days=3)

        response = self.post(
            description='Tamanho A3', priority='high', assignee=self.admin.pk, due_date=due.isoformat()
        )

        task = Task.objects.get(pk=response.data['id'])
        self.assertEqual(
            (task.description, task.priority, task.assignee, task.due_date), ('Tamanho A3', 'high', self.admin, due)
        )

    def test_defaults(self):
        task = Task.objects.get(pk=self.post().data['id'])

        self.assertEqual((task.priority, task.assignee, task.due_date, task.completed_at), ('medium', None, None, None))

    def test_new_task_goes_to_end_of_column(self):
        self.make_task('A', position=0)
        self.make_task('B', position=4)

        response = self.post()

        self.assertEqual(response.data['position'], 5)

    def test_task_created_in_done_column_is_completed(self):
        task = Task.objects.get(pk=self.post(column=self.done.pk).data['id'])

        self.assertIsNotNone(task.completed_at)

    def test_outsider_gets_404(self):
        self.assertEqual(self.post(self.outsider).status_code, 404)
        self.assertFalse(Task.objects.exists())

    def test_column_of_another_project_is_rejected(self):
        response = self.post(column=self.foreign_column.pk)

        self.assertEqual(response.status_code, 400)
        self.assertIn('column', response.data)

    def test_assignee_must_be_project_member(self):
        response = self.post(assignee=self.outsider.pk)

        self.assertEqual(response.status_code, 400)
        self.assertIn('assignee', response.data)

    def test_invalid_priority_is_rejected(self):
        response = self.post(priority='urgente')

        self.assertEqual(response.status_code, 400)
        self.assertIn('priority', response.data)

    def test_title_is_required(self):
        response = self.post(title='   ')

        self.assertEqual(response.status_code, 400)
        self.assertIn('title', response.data)

    def test_due_date_cannot_be_in_the_past(self):
        response = self.post(due_date=(self.today() - timedelta(days=1)).isoformat())

        self.assertEqual(response.status_code, 400)
        self.assertIn('due_date', response.data)

    def test_negative_position_is_rejected(self):
        response = self.post(position=-1)

        self.assertEqual(response.status_code, 400)
        self.assertIn('position', response.data)

    def test_created_by_and_completed_at_cannot_be_forged(self):
        response = self.post(created_by=self.owner.pk, completed_at=timezone.now().isoformat())

        task = Task.objects.get(pk=response.data['id'])
        self.assertEqual((task.created_by, task.completed_at), (self.member, None))


class ListAndDetailTests(TaskAPITestCase):
    def test_tasks_are_ordered_by_column_then_position(self):
        self.make_task('Fazendo', self.doing, position=0)
        self.make_task('Segunda', self.todo, position=1)
        self.make_task('Primeira', self.todo, position=0)
        self.make_task('Feita', self.done, position=0)

        response = client_for(self.member).get(TASKS, {'project': self.project.pk})

        self.assertEqual(self.titles(response), ['Primeira', 'Segunda', 'Fazendo', 'Feita'])

    def test_tasks_of_other_projects_are_isolated(self):
        self.make_task('Minha')
        self.make_task('Alheia', self.foreign_column)

        self.assertEqual(self.titles(client_for(self.member).get(TASKS)), ['Minha'])
        response = client_for(self.member).get(TASKS, {'project': self.other_project.pk})
        self.assertEqual(response.data['results'], [])

    def test_filters_by_column_assignee_and_priority(self):
        self.make_task('Alta minha', priority='high', assignee=self.member)
        self.make_task('Alta outra', self.doing, priority='high', assignee=self.admin)
        self.make_task('Baixa', priority='low')

        client = client_for(self.member)
        self.assertEqual(
            self.titles(client.get(TASKS, {'priority': 'high', 'assignee': self.member.pk})), ['Alta minha']
        )
        self.assertEqual(self.titles(client.get(TASKS, {'column': self.doing.pk})), ['Alta outra'])

    def test_overdue_flag(self):
        yesterday = self.today() - timedelta(days=1)
        self.make_task('Atrasada', due_date=yesterday)
        self.make_task('Feita', self.done, due_date=yesterday)
        self.make_task('Sem prazo')

        response = client_for(self.member).get(TASKS)

        flags = {item['title']: item['is_overdue'] for item in response.data['results']}
        self.assertEqual(flags, {'Atrasada': True, 'Feita': False, 'Sem prazo': False})

    def test_member_sees_detail(self):
        task = self.make_task(description='Detalhe', assignee=self.admin)

        response = client_for(self.member).get(task_url(task))

        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.data['description'], response.data['assignee']), ('Detalhe', self.admin.pk))

    def test_outsider_gets_404(self):
        self.assertEqual(client_for(self.outsider).get(task_url(self.make_task())).status_code, 404)


class UpdateTaskTests(TaskAPITestCase):
    def patch(self, task, user=None, **payload):
        return client_for(user or self.member).patch(task_url(task), payload)

    def test_any_member_edits_any_task(self):
        task = self.make_task(created_by=self.owner)

        response = self.patch(task, title='Novo título', priority='low', assignee=self.member.pk)

        self.assertEqual(response.status_code, 200)
        task.refresh_from_db()
        self.assertEqual((task.title, task.priority, task.assignee), ('Novo título', 'low', self.member))

    def test_assignee_can_be_removed(self):
        task = self.make_task(assignee=self.admin)

        client_for(self.member).patch(task_url(task), {'assignee': None}, format='json')

        task.refresh_from_db()
        self.assertIsNone(task.assignee)

    def test_moving_to_done_completes_and_back_reopens(self):
        task = self.make_task()

        self.patch(task, column=self.done.pk)
        task.refresh_from_db()
        self.assertIsNotNone(task.completed_at)

        self.patch(task, column=self.doing.pk)
        task.refresh_from_db()
        self.assertIsNone(task.completed_at)

    def test_moving_to_another_column_without_position_goes_to_end(self):
        self.make_task('Já lá', self.doing, position=7)
        task = self.make_task()

        response = self.patch(task, column=self.doing.pk)

        self.assertEqual(response.data['position'], 8)

    def test_cannot_move_to_column_of_another_project(self):
        task = self.make_task()

        response = self.patch(task, column=self.foreign_column.pk)

        self.assertEqual(response.status_code, 400)
        self.assertIn('column', response.data)
        task.refresh_from_db()
        self.assertEqual(task.column, self.todo)

    def test_project_cannot_change(self):
        own_other = make_project(self.member, name='Meu outro')
        task = self.make_task()

        response = self.patch(task, project=own_other.pk, column=own_other.columns.first().pk)

        self.assertEqual(response.status_code, 400)
        task.refresh_from_db()
        self.assertEqual(task.project, self.project)

    def test_assignee_must_be_project_member(self):
        response = self.patch(self.make_task(), assignee=self.outsider.pk)

        self.assertEqual(response.status_code, 400)
        self.assertIn('assignee', response.data)

    def test_keeping_an_old_past_due_date_is_allowed(self):
        # O frontend reenvia o formulário inteiro: o prazo vencido que já existia não pode travar a edição.
        old_due = self.today() - timedelta(days=5)
        task = self.make_task(due_date=old_due)

        response = self.patch(task, title='Ainda atrasada', due_date=old_due.isoformat())

        self.assertEqual(response.status_code, 200)

    def test_changing_to_past_due_date_is_rejected(self):
        response = self.patch(self.make_task(), due_date=(self.today() - timedelta(days=1)).isoformat())

        self.assertEqual(response.status_code, 400)
        self.assertIn('due_date', response.data)

    def test_outsider_gets_404(self):
        self.assertEqual(self.patch(self.make_task(), self.outsider, title='X').status_code, 404)


class DeleteTaskTests(TaskAPITestCase):
    def test_any_member_deletes_task(self):
        task = self.make_task(created_by=self.owner)

        response = client_for(self.member).delete(task_url(task))

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Task.objects.filter(pk=task.pk).exists())

    def test_outsider_gets_404(self):
        task = self.make_task()

        self.assertEqual(client_for(self.outsider).delete(task_url(task)).status_code, 404)
        self.assertTrue(Task.objects.filter(pk=task.pk).exists())

    def test_column_with_tasks_cannot_be_deleted(self):
        extra = Column.objects.create(project=self.project, name='Revisão', category='in_progress', position=3)
        self.make_task(column=extra)

        response = client_for(self.owner).delete(f'/api/columns/{extra.pk}/')

        self.assertEqual(response.status_code, 400)
        self.assertTrue(Column.objects.filter(pk=extra.pk).exists())


class RemovedMemberTests(TaskAPITestCase):
    def test_removing_member_unassigns_their_tasks_in_that_project(self):
        task = self.make_task(assignee=self.member)
        own_project = make_project(self.member, name='Do membro')
        own_task = self.make_task('Dele', own_project.columns.first(), assignee=self.member)
        membership = Membership.objects.get(project=self.project, user=self.member)

        response = client_for(self.owner).delete(f'/api/memberships/{membership.pk}/')

        self.assertEqual(response.status_code, 204)
        task.refresh_from_db()
        own_task.refresh_from_db()
        self.assertIsNone(task.assignee)
        self.assertEqual(own_task.assignee, self.member)
