from rest_framework.test import APITestCase

from core.models import ChecklistItem, Comment, Task

from .helpers import client_for, make_project, make_user

TASKS = '/api/tasks/'


class TaskListCardFieldsTests(APITestCase):
    """Card do Kanban: a lista traz descrição e contagem de comentários sem uma requisição por tarefa."""

    def setUp(self):
        self.owner = make_user('dono')
        self.member = make_user('membro')
        self.project = make_project(self.owner, members=[self.member])
        self.todo, self.doing, _ = self.project.columns.order_by('position')
        self.task = Task.objects.create(
            project=self.project, column=self.todo, title='Cartaz', description='Imprimir em A3'
        )
        for body in ('Primeiro', 'Segundo'):
            Comment.objects.create(task=self.task, author=self.owner, body=body)
        # Itens de checklist não podem multiplicar a contagem de comentários (joins).
        for position, text in enumerate(['A', 'B', 'C']):
            ChecklistItem.objects.create(task=self.task, text=text, position=position)

    def listed(self, title):
        results = client_for(self.member).get(TASKS, {'project': self.project.pk}).data['results']
        return next(task for task in results if task['title'] == title)

    def test_list_shows_description_and_comment_count(self):
        data = self.listed('Cartaz')

        self.assertEqual(data['description'], 'Imprimir em A3')
        self.assertEqual(data['comments_count'], 2)
        self.assertEqual(data['checklist_total'], 3)

    def test_task_without_comments_shows_zero(self):
        Task.objects.create(project=self.project, column=self.todo, title='Vazia')

        self.assertEqual(self.listed('Vazia')['comments_count'], 0)

    def test_detail_and_move_keep_comment_count(self):
        client = client_for(self.member)
        detail = client.get(f'{TASKS}{self.task.pk}/').data
        moved = client.post(
            f'{TASKS}{self.task.pk}/move/', {'column_id': self.doing.pk, 'position': 0}, format='json'
        ).data

        self.assertEqual(detail['comments_count'], 2)
        self.assertEqual(next(task for task in moved if task['id'] == self.task.pk)['comments_count'], 2)
