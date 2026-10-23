from datetime import timedelta

from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from core.models import Task

from .helpers import client_for, make_project, make_user

TASKS = '/api/tasks/'


class TaskFilterTestCase(APITestCase):
    def setUp(self):
        self.owner = make_user('dono')
        self.member = make_user('membro')
        self.outsider = make_user('externo')
        self.project = make_project(self.owner, members=[self.member])
        self.other_project = make_project(self.outsider, name='Outro projeto')
        self.todo, self.doing, self.done = self.project.columns.order_by('position')
        today = timezone.localdate()
        self.yesterday, self.tomorrow, self.next_week = (
            today - timedelta(days=1),
            today + timedelta(days=1),
            today + timedelta(days=7),
        )
        self.make('Cartaz atrasado', self.todo, priority='high', assignee=self.member, due_date=self.yesterday)
        self.make(
            'Roteiro em andamento',
            self.doing,
            priority='medium',
            due_date=self.tomorrow,
            description='Escrever a apresentação',
        )
        self.make('Convite pronto', self.done, priority='low', assignee=self.member, due_date=self.yesterday)
        self.make('Ideias soltas', self.todo, priority='low')
        self.make('Banner', self.doing, priority='high', assignee=self.owner, due_date=self.next_week)
        Task.objects.create(
            project=self.other_project,
            column=self.other_project.columns.first(),
            title='Cartaz alheio',
            due_date=self.yesterday,
        )

    def make(self, title, column, **extra):
        return Task.objects.create(project=self.project, column=column, title=title, **extra)

    def titles(self, params=None, user=None):
        response = client_for(user or self.member).get(TASKS, params or {})
        self.assertEqual(response.status_code, 200, response.data)
        return [item['title'] for item in response.data['results']]

    def title_set(self, params=None):
        return set(self.titles(params))


class AccessTests(TaskFilterTestCase):
    def test_requires_authentication(self):
        self.assertEqual(APIClient().get(TASKS, {'overdue': 'true'}).status_code, 401)

    def test_filters_never_reach_other_projects(self):
        self.assertNotIn('Cartaz alheio', self.title_set({'search': 'cartaz', 'overdue': 'true'}))
        self.assertEqual(self.titles({'project': self.other_project.pk}), [])


class FieldFilterTests(TaskFilterTestCase):
    def test_status_filters_by_column_category(self):
        self.assertEqual(self.title_set({'status': 'done'}), {'Convite pronto'})
        self.assertEqual(self.title_set({'status': 'in_progress'}), {'Roteiro em andamento', 'Banner'})

    def test_invalid_status_is_rejected(self):
        response = client_for(self.member).get(TASKS, {'status': 'bloqueada'})

        self.assertEqual(response.status_code, 400)
        self.assertIn('status', response.data)

    def test_column_filter(self):
        self.assertEqual(self.title_set({'column': self.todo.pk}), {'Cartaz atrasado', 'Ideias soltas'})

    def test_assignee_and_unassigned(self):
        self.assertEqual(self.title_set({'assignee': self.member.pk}), {'Cartaz atrasado', 'Convite pronto'})
        self.assertEqual(self.title_set({'assignee__isnull': 'true'}), {'Roteiro em andamento', 'Ideias soltas'})

    def test_priority(self):
        self.assertEqual(self.title_set({'priority': 'high'}), {'Cartaz atrasado', 'Banner'})

    def test_due_date_range(self):
        params = {'due_date__gte': self.tomorrow.isoformat(), 'due_date__lte': self.next_week.isoformat()}

        self.assertEqual(self.title_set(params), {'Roteiro em andamento', 'Banner'})

    def test_tasks_without_due_date(self):
        self.assertEqual(self.title_set({'due_date__isnull': 'true'}), {'Ideias soltas'})


class OverdueFilterTests(TaskFilterTestCase):
    def test_overdue_is_past_due_and_not_done(self):
        # Convite pronto também venceu ontem, mas está em coluna done; Ideias soltas não tem prazo.
        self.assertEqual(self.title_set({'overdue': 'true'}), {'Cartaz atrasado'})

    def test_not_overdue_includes_tasks_without_due_date(self):
        self.assertEqual(
            self.title_set({'overdue': 'false'}),
            {'Roteiro em andamento', 'Convite pronto', 'Ideias soltas', 'Banner'},
        )

    def test_task_due_today_is_not_overdue(self):
        self.make('Vence hoje', self.todo, due_date=timezone.localdate())

        self.assertNotIn('Vence hoje', self.title_set({'overdue': 'true'}))


class SearchAndOrderingTests(TaskFilterTestCase):
    def test_search_title_and_description_case_insensitive(self):
        self.assertEqual(self.title_set({'search': 'CARTAZ'}), {'Cartaz atrasado'})
        self.assertEqual(self.title_set({'search': 'apresentação'}), {'Roteiro em andamento'})

    def test_combined_filters(self):
        params = {'project': self.project.pk, 'assignee': self.member.pk, 'overdue': 'true', 'search': 'cartaz'}

        self.assertEqual(self.titles(params), ['Cartaz atrasado'])

        params['priority'] = 'low'
        self.assertEqual(self.titles(params), [])

    def test_default_order_is_the_board(self):
        self.assertEqual(
            self.titles({'project': self.project.pk}),
            ['Cartaz atrasado', 'Ideias soltas', 'Roteiro em andamento', 'Banner', 'Convite pronto'],
        )

    def test_order_by_due_date(self):
        self.assertEqual(
            self.titles({'ordering': 'due_date', 'due_date__isnull': 'false'})[-2:], ['Roteiro em andamento', 'Banner']
        )

    def test_order_by_priority_rank(self):
        ordered = self.titles({'ordering': '-priority_rank'})

        self.assertEqual(set(ordered[:2]), {'Cartaz atrasado', 'Banner'})
        self.assertEqual(set(ordered[-2:]), {'Convite pronto', 'Ideias soltas'})

    def test_unknown_ordering_field_is_ignored(self):
        self.assertEqual(len(self.titles({'ordering': 'my_role'})), 5)
