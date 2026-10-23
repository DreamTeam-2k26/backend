from django.test import TestCase

from core.models import ChecklistItem, Task

from .helpers import make_project, make_user


class ChecklistItemModelTests(TestCase):
    def setUp(self):
        project = make_project(make_user('dono'))
        self.task = Task.objects.create(project=project, column=project.columns.first(), title='Cartaz')

    def test_str_and_defaults(self):
        item = ChecklistItem.objects.create(task=self.task, text='Comprar cartolina')

        self.assertEqual(str(item), 'Comprar cartolina')
        self.assertEqual((item.is_done, item.position), (False, 0))

    def test_items_are_ordered_by_position(self):
        ChecklistItem.objects.create(task=self.task, text='Segundo', position=1)
        ChecklistItem.objects.create(task=self.task, text='Primeiro', position=0)

        self.assertEqual(list(self.task.checklist_items.values_list('text', flat=True)), ['Primeiro', 'Segundo'])

    def test_deleting_task_deletes_items(self):
        ChecklistItem.objects.create(task=self.task, text='Item')

        self.task.delete()

        self.assertFalse(ChecklistItem.objects.exists())

    def test_task_queryset_counts_checklist(self):
        ChecklistItem.objects.create(task=self.task, text='Feito', is_done=True)
        ChecklistItem.objects.create(task=self.task, text='Pendente')

        task = Task.objects.with_checklist_counts().get(pk=self.task.pk)

        self.assertEqual((task.checklist_total, task.checklist_done), (2, 1))
