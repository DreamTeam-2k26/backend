from django.test import TestCase

from core.models import SupportLink, Task

from .helpers import make_project, make_user


class SupportLinkModelTests(TestCase):
    def setUp(self):
        self.owner = make_user('dono')
        self.project = make_project(self.owner)
        self.task = Task.objects.create(project=self.project, column=self.project.columns.first(), title='Cartaz')

    def make_link(self, **extra):
        return SupportLink.objects.create(
            project=self.project, title='Edital', url='https://exemplo.com/edital', created_by=self.owner, **extra
        )

    def test_str(self):
        self.assertEqual(str(self.make_link()), 'Edital')

    def test_task_is_optional(self):
        self.assertIsNone(self.make_link().task)

    def test_deleting_task_deletes_its_links_only(self):
        general = self.make_link()
        self.make_link(task=self.task)

        self.task.delete()

        self.assertEqual(list(SupportLink.objects.all()), [general])

    def test_deleting_creator_keeps_link(self):
        link = self.make_link()

        self.owner.delete()
        link.refresh_from_db()

        self.assertIsNone(link.created_by)
