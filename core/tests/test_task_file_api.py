from unittest import mock

from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from rest_framework.test import APIClient, APITestCase

from chat.tests.helpers import PDF_BYTES, TempMediaMixin
from core.models import File, Task, TaskFile

from .helpers import client_for, make_project, make_user


def files_url(task, file=None):
    return f'/api/tasks/{task.pk}/files/' + (f'{file.pk}/' if file else '')


class TaskFileAPITestCase(TempMediaMixin, APITestCase):
    def setUp(self):
        self.owner = make_user('dono')
        self.member = make_user('membro')
        self.outsider = make_user('externo')
        self.project = make_project(self.owner, members=[self.member])
        self.other_project = make_project(self.outsider, name='Outro projeto')
        self.task = self.make_task(self.project, 'Cartaz')
        self.other_task = self.make_task(self.other_project, 'Alheia')
        self.edital = self.make_file(self.project, 'edital.pdf')
        self.ata = self.make_file(self.project, 'ata.pdf')
        self.foreign_file = self.make_file(self.other_project, 'alheio.pdf')

    def make_task(self, project, title):
        return Task.objects.create(project=project, column=project.columns.first(), title=title)

    def make_file(self, project, name):
        return File.objects.create(
            project=project,
            name=name,
            file=SimpleUploadedFile(name, PDF_BYTES),
            size=len(PDF_BYTES),
            mime_type='application/pdf',
            uploaded_by=self.owner,
        )

    def attach(self, file, user=None, task=None):
        return client_for(user or self.member).post(files_url(task or self.task), {'file': file.pk}, format='json')


class AuthenticationTests(TaskFileAPITestCase):
    def test_endpoints_require_authentication(self):
        TaskFile.objects.create(task=self.task, file=self.edital)
        anonymous = APIClient()
        for method, url in (
            ('get', files_url(self.task)),
            ('post', files_url(self.task)),
            ('delete', files_url(self.task, self.edital)),
        ):
            with self.subTest(method=method, url=url):
                self.assertEqual(getattr(anonymous, method)(url).status_code, 401)


class AttachTests(TaskFileAPITestCase):
    def test_member_attaches_existing_file_without_copying(self):
        response = self.attach(self.edital)

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual((response.data['id'], response.data['name']), (self.edital.pk, 'edital.pdf'))
        link = TaskFile.objects.get()
        self.assertEqual((link.task, link.file, link.attached_by), (self.task, self.edital, self.member))
        self.assertEqual(File.objects.count(), 3)

    def test_same_file_in_several_tasks(self):
        other = self.make_task(self.project, 'Banner')

        self.assertEqual(self.attach(self.edital).status_code, 201)
        self.assertEqual(self.attach(self.edital, task=other).status_code, 201)
        self.assertEqual(File.objects.count(), 3)

    def test_duplicate_attachment_is_rejected(self):
        self.attach(self.edital)

        response = self.attach(self.edital)

        self.assertEqual(response.status_code, 400)
        self.assertIn('file', response.data)
        self.assertEqual(TaskFile.objects.count(), 1)

    def test_concurrent_duplicate_is_rejected_without_500(self):
        with mock.patch.object(TaskFile.objects, 'create', side_effect=IntegrityError('duplicado')):
            response = self.attach(self.edital)

        self.assertEqual(response.status_code, 400)
        self.assertIn('file', response.data)

    def test_file_of_another_project_is_rejected(self):
        response = self.attach(self.foreign_file)

        self.assertEqual(response.status_code, 400)
        self.assertIn('file', response.data)
        self.assertFalse(TaskFile.objects.exists())

    def test_unknown_file_is_rejected(self):
        response = client_for(self.member).post(files_url(self.task), {'file': 999999}, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertIn('file', response.data)

    def test_file_is_required(self):
        response = client_for(self.member).post(files_url(self.task), {}, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertIn('file', response.data)

    def test_outsider_gets_404(self):
        self.assertEqual(self.attach(self.edital, user=self.outsider).status_code, 404)
        self.assertEqual(self.attach(self.foreign_file, user=self.member, task=self.other_task).status_code, 404)
        self.assertFalse(TaskFile.objects.exists())


class ListAndDetailTests(TaskFileAPITestCase):
    def setUp(self):
        super().setUp()
        self.attach(self.ata)
        self.attach(self.edital)

    def test_list_attached_files_with_metadata(self):
        response = client_for(self.member).get(files_url(self.task))

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['name'] for item in response.data], ['ata.pdf', 'edital.pdf'])
        first = response.data[0]
        self.assertEqual((first['size'], first['mime_type']), (len(PDF_BYTES), 'application/pdf'))
        self.assertEqual(first['download_url'], f'/api/files/{self.ata.pk}/download/')
        self.assertNotIn('file', first)

    def test_task_detail_includes_attached_files(self):
        response = client_for(self.member).get(f'/api/tasks/{self.task.pk}/')

        self.assertEqual([item['id'] for item in response.data['files']], [self.ata.pk, self.edital.pk])

    def test_task_detail_files_are_read_only(self):
        client_for(self.member).patch(f'/api/tasks/{self.task.pk}/', {'files': [self.foreign_file.pk]}, format='json')

        self.assertEqual(set(self.task.task_files.values_list('file', flat=True)), {self.ata.pk, self.edital.pk})

    def test_task_without_files_shows_empty_list(self):
        other = self.make_task(self.project, 'Sem anexos')

        self.assertEqual(client_for(self.member).get(f'/api/tasks/{other.pk}/').data['files'], [])
        self.assertEqual(client_for(self.member).get(files_url(other)).data, [])

    def test_attached_file_downloads_only_for_members(self):
        url = client_for(self.member).get(files_url(self.task)).data[0]['download_url']

        member_download = client_for(self.member).get(url)
        self.assertEqual(member_download.status_code, 200)
        self.assertEqual(b''.join(member_download.streaming_content), PDF_BYTES)
        self.assertEqual(client_for(self.outsider).get(url).status_code, 404)

    def test_outsider_gets_404(self):
        self.assertEqual(client_for(self.outsider).get(files_url(self.task)).status_code, 404)


class DetachTests(TaskFileAPITestCase):
    def setUp(self):
        super().setUp()
        self.attach(self.edital)

    def test_member_detaches_without_deleting_file(self):
        stored = self.edital.file.name

        with self.captureOnCommitCallbacks(execute=True):
            response = client_for(self.owner).delete(files_url(self.task, self.edital))

        self.assertEqual(response.status_code, 204)
        self.assertFalse(TaskFile.objects.exists())
        self.assertTrue(File.objects.filter(pk=self.edital.pk).exists())
        self.assertTrue(default_storage.exists(stored))

    def test_detaching_file_not_attached_returns_404(self):
        self.assertEqual(client_for(self.member).delete(files_url(self.task, self.ata)).status_code, 404)

    def test_outsider_gets_404(self):
        self.assertEqual(client_for(self.outsider).delete(files_url(self.task, self.edital)).status_code, 404)
        self.assertTrue(TaskFile.objects.exists())

    def test_deleting_task_keeps_file_in_project(self):
        client_for(self.member).delete(f'/api/tasks/{self.task.pk}/')

        self.assertTrue(File.objects.filter(pk=self.edital.pk).exists())
