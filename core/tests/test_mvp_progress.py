"""Progresso, atrasadas e reuniões coerentes entre as telas, conforme as tarefas andam no quadro.

O cálculo de cada número já é testado na sua task; aqui o que importa é que card, dashboard e filtro de atrasadas
contam a mesma história depois de movimentações reais pelo endpoint de drag-and-drop.
"""

from datetime import timedelta

from django.utils import timezone
from rest_framework.test import APITestCase

from core.models import Column, Meeting, Task

from .helpers import client_for, make_project, make_user


class BoardProgressTests(APITestCase):
    def setUp(self):
        self.owner = make_user('dono')
        self.member = make_user('membro')
        self.project = make_project(self.owner, members=[self.member])
        self.todo, self.doing, self.done = self.project.columns.order_by('position')
        yesterday = timezone.localdate() - timedelta(days=1)
        self.late = Task.objects.create(project=self.project, column=self.todo, title='Atrasada', due_date=yesterday)
        self.no_due = Task.objects.create(project=self.project, column=self.todo, title='Sem prazo')
        self.future = Task.objects.create(
            project=self.project, column=self.doing, title='Futura', due_date=timezone.localdate() + timedelta(days=5)
        )
        self.client_member = client_for(self.member)

    def move(self, task, column):
        response = self.client_member.post(
            f'/api/tasks/{task.pk}/move/', {'column_id': column.pk, 'position': 0}, format='json'
        )
        self.assertEqual(response.status_code, 200, response.data)

    def screens(self):
        """O que cada tela mostra: card da lista de projetos, dashboard e filtro de atrasadas."""
        card = self.client_member.get('/api/projects/').data['results'][0]
        dashboard = self.client_member.get(f'/api/projects/{self.project.pk}/dashboard/').data
        overdue = self.client_member.get('/api/tasks/', {'project': self.project.pk, 'overdue': 'true'}).data
        return {
            'card': card['progress'],
            'dashboard': dashboard['progress'],
            'concluídas': dashboard['done_tasks'],
            'atrasadas (dashboard)': dashboard['overdue_tasks'],
            'atrasadas (filtro)': sorted(task['title'] for task in overdue['results']),
        }

    def test_screens_agree_as_tasks_move(self):
        self.assertEqual(
            self.screens(),
            {
                'card': 0,
                'dashboard': 0,
                'concluídas': 0,
                'atrasadas (dashboard)': 1,
                'atrasadas (filtro)': ['Atrasada'],
            },
        )

        # Concluir a atrasada tira ela das atrasadas e sobe o progresso em todas as telas.
        self.move(self.late, self.done)
        self.assertEqual(
            self.screens(),
            {'card': 33, 'dashboard': 33, 'concluídas': 1, 'atrasadas (dashboard)': 0, 'atrasadas (filtro)': []},
        )

        # Uma coluna "done" personalizada também conta como concluída.
        delivered = Column.objects.create(project=self.project, name='Entregue', category='done', position=3)
        self.move(self.future, delivered)
        self.assertEqual(self.screens()['dashboard'], 67)

        # Reabrir a atrasada a devolve às atrasadas e baixa o progresso.
        self.move(self.late, self.doing)
        self.assertEqual(
            self.screens(),
            {
                'card': 33,
                'dashboard': 33,
                'concluídas': 1,
                'atrasadas (dashboard)': 1,
                'atrasadas (filtro)': ['Atrasada'],
            },
        )
        self.late.refresh_from_db()
        self.assertIsNone(self.late.completed_at)

    def test_project_with_all_tasks_removed_goes_back_to_zero(self):
        self.move(self.late, self.done)
        Task.objects.filter(project=self.project).delete()

        screens = self.screens()

        self.assertEqual((screens['card'], screens['dashboard'], screens['atrasadas (dashboard)']), (0, 0, 0))


class UpcomingMeetingTests(APITestCase):
    def test_next_meeting_for_the_overview_only_for_members(self):
        owner = make_user('dono')
        outsider = make_user('externo')
        project = make_project(owner)
        now = timezone.now()
        Meeting.objects.create(project=project, title='Passada', starts_at=now - timedelta(days=1), url='https://x.com')
        Meeting.objects.create(project=project, title='Próxima', starts_at=now + timedelta(days=1), url='https://x.com')
        Meeting.objects.create(project=project, title='Depois', starts_at=now + timedelta(days=9), url='https://x.com')

        params = {'project': project.pk, 'upcoming': 'true'}
        upcoming = client_for(owner).get('/api/meetings/', params).data['results']

        self.assertEqual([meeting['title'] for meeting in upcoming], ['Próxima', 'Depois'])
        self.assertEqual(client_for(outsider).get('/api/meetings/', params).data['results'], [])
