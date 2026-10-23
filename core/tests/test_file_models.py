from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from chat.tests.helpers import PDF_BYTES, TempMediaMixin
from core.models import File, Folder
from core.models.file import project_file_path

from .helpers import make_project, make_user


class ProjectFilePathTests(TestCase):
    def setUp(self):
        self.project = make_project(make_user('dono'))

    def test_path_is_random_inside_project_folder(self):
        path = project_file_path(File(project=self.project), 'Relatório Final.PDF')

        self.assertTrue(path.startswith(f'project_files/{self.project.pk}/'))
        self.assertTrue(path.endswith('.pdf'))
        self.assertNotIn('Relat', path)

    def test_client_filename_cannot_escape_storage_folder(self):
        for name in ('../../etc/passwd', '..\\..\\windows\\win.ini', '/abs/path.txt', 'a.p/../hp'):
            with self.subTest(name=name):
                path = project_file_path(File(project=self.project), name)
                self.assertNotIn('..', path)
                self.assertTrue(path.startswith(f'project_files/{self.project.pk}/'))
                self.assertEqual(path.count('/'), 2)


class FileModelTests(TempMediaMixin, TestCase):
    def setUp(self):
        self.owner = make_user('dono')
        self.project = make_project(self.owner)
        self.folder = Folder.objects.create(project=self.project, name='Documentos')

    def make(self, folder=None):
        return File.objects.create(
            project=self.project,
            folder=folder,
            name='edital.pdf',
            file=SimpleUploadedFile('edital.pdf', PDF_BYTES),
            size=len(PDF_BYTES),
            mime_type='application/pdf',
            uploaded_by=self.owner,
        )

    def test_str(self):
        self.assertEqual(str(self.make()), 'edital.pdf')

    def test_deleting_folder_deletes_its_files(self):
        root_file = self.make()
        self.make(self.folder)

        self.folder.delete()

        self.assertEqual(list(File.objects.all()), [root_file])

    def test_deleting_uploader_keeps_file(self):
        file = self.make()

        self.owner.delete()
        file.refresh_from_db()

        self.assertIsNone(file.uploaded_by)
