from django.db.models import RestrictedError
from django.test import TestCase

from core.models import Column, Project, Task

from .helpers import make_project, make_user


class TaskModelTests(TestCase):
    def setUp(self):
        self.owner = make_user('dono')
        self.project = make_project(self.owner)
        self.todo, self.doing, self.done = self.project.columns.order_by('position')

    def make_task(self, column=None, **extra):
        return Task.objects.create(project=self.project, column=column or self.todo, title='Cartaz', **extra)

    def test_priority_choices(self):
        self.assertEqual(Task.Priority.values, ['low', 'medium', 'high'])
        self.assertEqual(self.make_task().priority, Task.Priority.MEDIUM)

    def test_str(self):
        self.assertEqual(str(self.make_task()), 'Cartaz')

    def test_completed_at_follows_done_category(self):
        task = self.make_task()
        self.assertIsNone(task.completed_at)

        task.column = self.done
        task.save()
        self.assertIsNotNone(task.completed_at)

        task.column = self.doing
        task.save()
        self.assertIsNone(task.completed_at)

    def test_completed_at_kept_while_staying_in_done(self):
        task = self.make_task(self.done)
        first = task.completed_at

        task.title = 'Cartaz final'
        task.save()

        self.assertEqual(task.completed_at, first)

    def test_column_with_tasks_is_restricted(self):
        self.make_task(self.doing)

        with self.assertRaises(RestrictedError):
            self.doing.delete()

    def test_deleting_project_deletes_its_tasks(self):
        self.make_task()
        self.make_task(self.done)

        self.project.delete()

        self.assertFalse(Task.objects.exists())
        self.assertFalse(Column.objects.filter(project_id=self.project.pk).exists())
        self.assertFalse(Project.objects.filter(pk=self.project.pk).exists())
