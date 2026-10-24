from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase

from chat.tests.helpers import PDF_BYTES, TempMediaMixin
from core.models import File, Task, TaskFile

from .helpers import make_project, make_user


class TaskFileModelTests(TempMediaMixin, TestCase):
    def setUp(self):
        self.owner = make_user('dono')
        project = make_project(self.owner)
        self.task = Task.objects.create(project=project, column=project.columns.first(), title='Cartaz')
        self.file = File.objects.create(
            project=project,
            name='edital.pdf',
            file=SimpleUploadedFile('edital.pdf', PDF_BYTES),
            size=len(PDF_BYTES),
            mime_type='application/pdf',
        )

    def test_str(self):
        link = TaskFile.objects.create(task=self.task, file=self.file)

        self.assertEqual(str(link), 'edital.pdf em Cartaz')

    def test_same_file_cannot_be_attached_twice(self):
        TaskFile.objects.create(task=self.task, file=self.file)

        with self.assertRaises(IntegrityError), transaction.atomic():
            TaskFile.objects.create(task=self.task, file=self.file)

    def test_deleting_task_keeps_file(self):
        TaskFile.objects.create(task=self.task, file=self.file)

        self.task.delete()

        self.assertFalse(TaskFile.objects.exists())
        self.assertTrue(File.objects.filter(pk=self.file.pk).exists())

    def test_deleting_file_removes_link(self):
        TaskFile.objects.create(task=self.task, file=self.file)

        self.file.delete()

        self.assertFalse(TaskFile.objects.exists())
        self.assertTrue(Task.objects.filter(pk=self.task.pk).exists())
