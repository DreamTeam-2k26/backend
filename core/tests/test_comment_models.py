from django.test import TestCase

from core.models import Comment, Task

from .helpers import make_project, make_user


class CommentModelTests(TestCase):
    def setUp(self):
        self.owner = make_user('dono')
        self.author = make_user('autor')
        project = make_project(self.owner, members=[self.author])
        self.task = Task.objects.create(project=project, column=project.columns.first(), title='Cartaz')

    def test_str(self):
        comment = Comment.objects.create(task=self.task, author=self.author, body='Vou imprimir amanhã de manhã')

        self.assertEqual(str(comment), f'{self.author}: Vou imprimir amanhã de manhã')

    def test_deleted_author_keeps_comment_as_history(self):
        comment = Comment.objects.create(task=self.task, author=self.author, body='Histórico')

        self.author.delete()
        comment.refresh_from_db()

        self.assertIsNone(comment.author)
        self.assertEqual(comment.body, 'Histórico')
        self.assertEqual(str(comment), 'Usuário removido: Histórico')

    def test_deleting_task_deletes_comments(self):
        Comment.objects.create(task=self.task, author=self.author, body='Some com a tarefa')

        self.task.delete()

        self.assertFalse(Comment.objects.exists())
