import io
import zipfile

from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from PIL import Image as PillowImage
from rest_framework.test import APIClient, APITestCase

from chat.tests.helpers import HTML_BYTES, PDF_BYTES, TempMediaMixin
from core.models import File, Folder

from .helpers import client_for, make_project, make_user

FILES = '/api/files/'


def file_url(file, suffix=''):
    return f'{FILES}{file.pk}/{suffix}'


def png_bytes():
    buffer = io.BytesIO()
    PillowImage.new('RGB', (2, 2)).save(buffer, format='PNG')
    return buffer.getvalue()


def docx_bytes():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr(
            '[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>'
        )
        archive.writestr('_rels/.rels', '<Relationships/>')
        archive.writestr('word/document.xml', '<x/>')
    return buffer.getvalue()


def upload(name, content, content_type='application/octet-stream'):
    return SimpleUploadedFile(name, content, content_type=content_type)


class FileAPITestCase(TempMediaMixin, APITestCase):
    def setUp(self):
        self.owner = make_user('dono')
        self.member = make_user('membro')
        self.outsider = make_user('externo')
        self.project = make_project(self.owner, members=[self.member])
        self.other_project = make_project(self.outsider, name='Outro projeto')
        self.docs = Folder.objects.create(project=self.project, name='Documentos')
        self.images = Folder.objects.create(project=self.project, name='Imagens')
        self.foreign_folder = Folder.objects.create(project=self.other_project, name='Alheia')

    def post(self, user=None, file=None, **payload):
        data = {'project': self.project.pk, 'file': file or upload('edital.pdf', PDF_BYTES), **payload}
        return client_for(user or self.member).post(FILES, data, format='multipart')

    def uploaded(self, **payload):
        response = self.post(**payload)
        self.assertEqual(response.status_code, 201, response.data)
        return File.objects.get(pk=response.data['id'])


class AuthenticationTests(FileAPITestCase):
    def test_endpoints_require_authentication(self):
        file = self.uploaded()
        anonymous = APIClient()
        requests = [
            ('get', FILES),
            ('post', FILES),
            ('get', file_url(file)),
            ('get', file_url(file, 'download/')),
            ('patch', file_url(file)),
            ('delete', file_url(file)),
        ]
        for method, url in requests:
            with self.subTest(method=method, url=url):
                self.assertEqual(getattr(anonymous, method)(url).status_code, 401)


class UploadTests(FileAPITestCase):
    def test_member_uploads_file_with_metadata(self):
        file = self.uploaded(folder=self.docs.pk)

        self.assertEqual(
            (file.project, file.folder, file.name, file.size, file.mime_type, file.uploaded_by),
            (self.project, self.docs, 'edital.pdf', len(PDF_BYTES), 'application/pdf', self.member),
        )
        self.assertTrue(default_storage.exists(file.file.name))

    def test_upload_to_root_and_custom_name(self):
        file = self.uploaded(name='Edital 2026.pdf')

        self.assertEqual((file.folder, file.name), (None, 'Edital 2026.pdf'))

    def test_allowed_types(self):
        cases = [
            ('foto.png', png_bytes()),
            ('ata.txt', 'Ata da reunião'.encode()),
            ('notas.csv', b'a,b\n1,2\n'),
            ('trabalho.docx', docx_bytes()),
        ]
        for name, content in cases:
            with self.subTest(name=name):
                self.assertEqual(self.post(file=upload(name, content)).status_code, 201)

    def test_response_never_exposes_storage_path(self):
        response = self.post()

        self.assertNotIn('file', response.data)
        self.assertNotIn('project_files', str(response.data))
        self.assertEqual(response.data['download_url'], f'/api/files/{response.data["id"]}/download/')

    def test_storage_path_ignores_client_filename(self):
        file = self.uploaded(file=upload('../../../../etc/relatorio.pdf', PDF_BYTES))

        self.assertTrue(file.file.name.startswith(f'project_files/{self.project.pk}/'))
        self.assertNotIn('..', file.file.name)
        self.assertEqual(file.name, 'relatorio.pdf')

    def test_extension_not_allowed_is_rejected(self):
        for name in ('script.sh', 'pagina.html', 'desenho.svg', 'sem_extensao'):
            with self.subTest(name=name):
                response = self.post(file=upload(name, b'conteudo qualquer'))
                self.assertEqual(response.status_code, 400)
                self.assertIn('file', response.data)

    def test_content_must_match_extension(self):
        cases = [('relatorio.pdf', b'MZ\x90\x00' + b'\x00' * 200), ('ata.txt', HTML_BYTES), ('foto.png', PDF_BYTES)]
        for name, content in cases:
            with self.subTest(name=name):
                response = self.post(file=upload(name, content))
                self.assertEqual(response.status_code, 400)
                self.assertIn('file', response.data)
        self.assertFalse(File.objects.exists())

    def test_empty_file_is_rejected(self):
        response = self.post(file=upload('vazio.txt', b''))

        self.assertEqual(response.status_code, 400)
        self.assertIn('file', response.data)

    @override_settings(PROJECT_FILE_MAX_UPLOAD_MB=1)
    def test_too_large_file_is_rejected(self):
        response = self.post(file=upload('grande.txt', b'a' * (1024 * 1024 + 1)))

        self.assertEqual(response.status_code, 400)
        self.assertIn('file', response.data)

    def test_folder_of_another_project_is_rejected(self):
        response = self.post(folder=self.foreign_folder.pk)

        self.assertEqual(response.status_code, 400)
        self.assertIn('folder', response.data)
        self.assertFalse(File.objects.exists())

    def test_outsider_gets_404(self):
        self.assertEqual(self.post(self.outsider).status_code, 404)
        self.assertFalse(File.objects.exists())

    def test_uploaded_by_cannot_be_forged(self):
        file = self.uploaded(uploaded_by=self.owner.pk)

        self.assertEqual(file.uploaded_by, self.member)

    def test_profile_picture_upload_still_works(self):
        response = client_for(self.member).post(
            '/api/media/images/', {'file': upload('foto.png', png_bytes(), 'image/png')}, format='multipart'
        )

        self.assertEqual(response.status_code, 201, response.data)


class ListAndDetailTests(FileAPITestCase):
    def setUp(self):
        super().setUp()
        self.root_file = self.uploaded(name='edital.pdf')
        self.doc_file = self.uploaded(name='Ata.txt', folder=self.docs.pk, file=upload('ata.txt', b'Ata'))

    def names(self, params, user=None):
        response = client_for(user or self.member).get(FILES, params)
        self.assertEqual(response.status_code, 200, response.data)
        return [item['name'] for item in response.data['results']]

    def test_list_by_folder_and_root(self):
        self.assertEqual(self.names({'folder': self.docs.pk}), ['Ata.txt'])
        self.assertEqual(self.names({'project': self.project.pk, 'folder__isnull': 'true'}), ['edital.pdf'])

    def test_files_of_other_projects_are_isolated(self):
        self.assertEqual(self.names({'project': self.other_project.pk}), [])
        self.assertEqual(self.names({}, self.outsider), [])

    def test_detail_metadata(self):
        response = client_for(self.member).get(file_url(self.doc_file))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            (response.data['name'], response.data['size'], response.data['mime_type'], response.data['folder']),
            ('Ata.txt', 3, 'text/plain', self.docs.pk),
        )

    def test_outsider_gets_404(self):
        self.assertEqual(client_for(self.outsider).get(file_url(self.root_file)).status_code, 404)


class DownloadTests(FileAPITestCase):
    def setUp(self):
        super().setUp()
        self.file = self.uploaded(name='Edital final.pdf')

    def test_member_downloads_file_as_attachment(self):
        response = client_for(self.owner).get(file_url(self.file, 'download/'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), PDF_BYTES)
        self.assertIn('attachment', response['Content-Disposition'])
        self.assertIn('Edital final.pdf', response['Content-Disposition'])
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        self.assertIn('sandbox', response['Content-Security-Policy'])

    def test_outsider_download_is_denied(self):
        self.assertEqual(client_for(self.outsider).get(file_url(self.file, 'download/')).status_code, 404)

    def test_removed_member_loses_download(self):
        self.project.memberships.filter(user=self.member).delete()

        self.assertEqual(client_for(self.member).get(file_url(self.file, 'download/')).status_code, 404)

    def test_missing_storage_object_returns_404(self):
        default_storage.delete(self.file.file.name)

        self.assertEqual(client_for(self.member).get(file_url(self.file, 'download/')).status_code, 404)


class RenameAndMoveTests(FileAPITestCase):
    def setUp(self):
        super().setUp()
        self.file = self.uploaded(folder=self.docs.pk)

    def patch(self, user=None, **payload):
        return client_for(user or self.member).patch(file_url(self.file), payload, format='json')

    def test_rename(self):
        self.assertEqual(self.patch(name='Edital revisado.pdf').status_code, 200)
        self.file.refresh_from_db()
        self.assertEqual(self.file.name, 'Edital revisado.pdf')

    def test_name_cannot_be_empty_or_contain_path(self):
        for name in ('   ', '../segredo.pdf', 'pasta/arquivo.pdf', 'pasta\\arquivo.pdf'):
            with self.subTest(name=name):
                response = self.patch(name=name)
                self.assertEqual(response.status_code, 400)
                self.assertIn('name', response.data)

    def test_move_between_folders_and_to_root(self):
        stored = self.file.file.name

        self.assertEqual(self.patch(folder=self.images.pk).status_code, 200)
        self.file.refresh_from_db()
        self.assertEqual(self.file.folder, self.images)

        self.assertEqual(self.patch(folder=None).status_code, 200)
        self.file.refresh_from_db()
        self.assertIsNone(self.file.folder)
        self.assertEqual(self.file.file.name, stored)

    def test_cannot_move_to_folder_of_another_project(self):
        response = self.patch(folder=self.foreign_folder.pk)

        self.assertEqual(response.status_code, 400)
        self.assertIn('folder', response.data)
        self.file.refresh_from_db()
        self.assertEqual(self.file.folder, self.docs)

    def test_project_cannot_change_even_for_root_file(self):
        # Arquivo na raiz: a regra "pasta de outro projeto" não se aplica, só a de projeto fixo.
        root_file = self.uploaded()
        own_other = make_project(self.member, name='Meu outro')

        response = client_for(self.member).patch(file_url(root_file), {'project': own_other.pk}, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertIn('project', response.data)
        root_file.refresh_from_db()
        self.assertEqual(root_file.project, self.project)

    def test_project_content_and_uploader_cannot_change(self):
        own_other = make_project(self.member, name='Meu outro')
        stored = self.file.file.name

        self.patch(uploaded_by=self.owner.pk, size=1, mime_type='text/html')
        response = self.patch(project=own_other.pk)
        self.assertEqual(response.status_code, 400)
        replace = client_for(self.member).patch(
            file_url(self.file), {'file': upload('outro.pdf', PDF_BYTES)}, format='multipart'
        )
        self.assertEqual(replace.status_code, 400)

        self.file.refresh_from_db()
        self.assertEqual(
            (self.file.project, self.file.uploaded_by, self.file.size, self.file.mime_type, self.file.file.name),
            (self.project, self.member, len(PDF_BYTES), 'application/pdf', stored),
        )

    def test_outsider_gets_404(self):
        self.assertEqual(self.patch(self.outsider, name='X.pdf').status_code, 404)


class DeleteTests(FileAPITestCase):
    def test_member_deletes_file_and_storage_object(self):
        file = self.uploaded()
        stored = file.file.name

        with self.captureOnCommitCallbacks(execute=True):
            response = client_for(self.member).delete(file_url(file))

        self.assertEqual(response.status_code, 204)
        self.assertFalse(File.objects.filter(pk=file.pk).exists())
        self.assertFalse(default_storage.exists(stored))

    def test_deleting_folder_removes_files_from_storage(self):
        file = self.uploaded(folder=self.docs.pk)
        stored = file.file.name

        with self.captureOnCommitCallbacks(execute=True):
            client_for(self.member).delete(f'/api/folders/{self.docs.pk}/')

        self.assertFalse(default_storage.exists(stored))

    def test_outsider_gets_404(self):
        file = self.uploaded()

        self.assertEqual(client_for(self.outsider).delete(file_url(file)).status_code, 404)
        self.assertTrue(default_storage.exists(file.file.name))
