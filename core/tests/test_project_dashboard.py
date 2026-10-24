from datetime import timedelta

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from core.models import Column, Task

from .helpers import client_for, make_project, make_user


def dashboard_url(project):
    return f'/api/projects/{project.pk}/dashboard/'


class ProjectDashboardTests(APITestCase):
    def setUp(self):
        self.owner = make_user('dono')
        self.member = make_user('membro')
        self.outsider = make_user('externo')
        self.project = make_project(self.owner, members=[self.member])
        self.todo, self.doing, self.done = self.project.columns.order_by('position')
        self.yesterday = timezone.localdate() - timedelta(days=1)

    def make(self, column, project=None, **extra):
        project = project or self.project
        return Task.objects.create(project=project, column=column, title='Tarefa', **extra)

    def dashboard(self, user=None):
        response = client_for(user or self.member).get(dashboard_url(self.project))
        self.assertEqual(response.status_code, 200, getattr(response, 'data', None))
        return response.data

    def test_requires_authentication(self):
        self.assertEqual(APIClient().get(dashboard_url(self.project)).status_code, 401)

    def test_outsider_gets_404(self):
        self.assertEqual(client_for(self.outsider).get(dashboard_url(self.project)).status_code, 404)

    def test_project_without_tasks_is_all_zero(self):
        self.assertEqual(
            self.dashboard(),
            {
                'total_tasks': 0,
                'done_tasks': 0,
                'pending_tasks': 0,
                'todo_tasks': 0,
                'in_progress_tasks': 0,
                'overdue_tasks': 0,
                'progress': 0,
            },
        )

    def test_counts_and_progress(self):
        extra_done = Column.objects.create(project=self.project, name='Entregue', category='done', position=3)
        self.make(self.todo, due_date=self.yesterday)
        self.make(self.todo)
        self.make(self.doing, due_date=self.yesterday)
        self.make(self.done, due_date=self.yesterday)
        self.make(extra_done)
        self.make(self.doing, due_date=timezone.localdate())

        data = self.dashboard()

        self.assertEqual(
            data,
            {
                'total_tasks': 6,
                'done_tasks': 2,
                'pending_tasks': 4,
                'todo_tasks': 2,
                'in_progress_tasks': 2,
                'overdue_tasks': 2,
                'progress': 33,
            },
        )

    def test_tasks_without_due_date_are_never_overdue(self):
        self.make(self.todo)
        self.make(self.doing)

        self.assertEqual(self.dashboard()['overdue_tasks'], 0)

    def test_other_projects_do_not_count(self):
        other = make_project(self.member, name='Outro')
        other_todo, _, other_done = other.columns.order_by('position')
        self.make(other_done, project=other)
        self.make(other_todo, project=other, due_date=self.yesterday)
        self.make(self.todo)

        data = self.dashboard()

        self.assertEqual((data['total_tasks'], data['done_tasks'], data['overdue_tasks']), (1, 0, 0))

    def test_any_member_sees_dashboard(self):
        self.make(self.done)

        self.assertEqual(self.dashboard(self.owner)['progress'], 100)
        self.assertEqual(self.dashboard(self.member)['progress'], 100)

    def test_matches_progress_of_project_list(self):
        self.make(self.todo)
        self.make(self.todo)
        self.make(self.done)

        card = client_for(self.member).get('/api/projects/').data['results'][0]

        self.assertEqual(card['progress'], self.dashboard()['progress'])

    def test_query_count_does_not_grow_with_tasks(self):
        self.make(self.todo)
        with CaptureQueriesContext(connection) as few:
            self.dashboard()

        for _ in range(10):
            self.make(self.doing, due_date=self.yesterday)
        with CaptureQueriesContext(connection) as many:
            self.dashboard()

        self.assertEqual(len(many), len(few))
