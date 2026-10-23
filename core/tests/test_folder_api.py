from rest_framework.test import APIClient, APITestCase

from core.models import Folder

from .helpers import client_for, make_project, make_user

FOLDERS = '/api/folders/'


def folder_url(folder):
    return f'{FOLDERS}{folder.pk}/'


class FolderAPITestCase(APITestCase):
    def setUp(self):
        self.owner = make_user('dono')
        self.member = make_user('membro')
        self.outsider = make_user('externo')
        self.project = make_project(self.owner, members=[self.member])
        self.other_project = make_project(self.outsider, name='Outro projeto')
        # docs/atas/2026 e imagens na raiz
        self.docs = self.make('Documentos')
        self.atas = self.make('Atas', self.docs)
        self.year = self.make('2026', self.atas)
        self.images = self.make('Imagens')
        self.foreign = Folder.objects.create(project=self.other_project, name='Alheia')

    def make(self, name, parent=None):
        return Folder.objects.create(project=self.project, parent=parent, name=name, created_by=self.owner)

    def names(self, params, user=None):
        response = client_for(user or self.member).get(FOLDERS, params)
        self.assertEqual(response.status_code, 200, response.data)
        return [item['name'] for item in response.data['results']]


class AuthenticationTests(FolderAPITestCase):
    def test_endpoints_require_authentication(self):
        anonymous = APIClient()
        url = folder_url(self.docs)
        for method, path in (('get', FOLDERS), ('post', FOLDERS), ('get', url), ('patch', url), ('delete', url)):
            with self.subTest(method=method, path=path):
                self.assertEqual(getattr(anonymous, method)(path).status_code, 401)


class ListAndDetailTests(FolderAPITestCase):
    def test_root_folders_of_project(self):
        self.assertEqual(self.names({'project': self.project.pk, 'parent__isnull': 'true'}), ['Documentos', 'Imagens'])

    def test_contents_of_a_folder(self):
        self.make('apresentação', self.docs)

        self.assertEqual(self.names({'parent': self.docs.pk}), ['apresentação', 'Atas'])

    def test_children_count(self):
        response = client_for(self.member).get(FOLDERS, {'project': self.project.pk, 'parent__isnull': 'true'})

        counts = {item['name']: item['children_count'] for item in response.data['results']}
        self.assertEqual(counts, {'Documentos': 1, 'Imagens': 0})

    def test_folders_of_other_projects_are_isolated(self):
        self.assertNotIn('Alheia', self.names({}))
        self.assertEqual(self.names({'project': self.other_project.pk}), [])

    def test_detail_has_breadcrumb_path(self):
        response = client_for(self.member).get(folder_url(self.year))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data['path'],
            [
                {'id': self.docs.pk, 'name': 'Documentos'},
                {'id': self.atas.pk, 'name': 'Atas'},
                {'id': self.year.pk, 'name': '2026'},
            ],
        )

    def test_outsider_gets_404(self):
        self.assertEqual(client_for(self.outsider).get(folder_url(self.docs)).status_code, 404)


class CreateFolderTests(FolderAPITestCase):
    def post(self, user=None, **payload):
        return client_for(user or self.member).post(FOLDERS, {'project': self.project.pk, **payload}, format='json')

    def test_member_creates_root_folder(self):
        response = self.post(name='Planilhas')

        self.assertEqual(response.status_code, 201)
        folder = Folder.objects.get(pk=response.data['id'])
        self.assertEqual((folder.project, folder.parent, folder.created_by), (self.project, None, self.member))

    def test_member_creates_subfolder(self):
        response = self.post(name='Fotos', parent=self.images.pk)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(Folder.objects.get(pk=response.data['id']).parent, self.images)

    def test_parent_of_another_project_is_rejected(self):
        response = self.post(name='Invasão', parent=self.foreign.pk)

        self.assertEqual(response.status_code, 400)
        self.assertIn('parent', response.data)
        self.assertFalse(Folder.objects.filter(parent=self.foreign).exists())

    def test_name_is_required(self):
        response = self.post(name='   ')

        self.assertEqual(response.status_code, 400)
        self.assertIn('name', response.data)

    def test_duplicate_name_in_same_place_is_rejected(self):
        for name, parent in (('documentos', None), ('ATAS', self.docs.pk)):
            with self.subTest(name=name):
                response = self.post(name=name, parent=parent)
                self.assertEqual(response.status_code, 400)
                self.assertIn('name', response.data)

    def test_same_name_in_another_place_is_allowed(self):
        self.assertEqual(self.post(name='Atas').status_code, 201)
        self.assertEqual(self.post(name='Atas', parent=self.images.pk).status_code, 201)

    def test_outsider_gets_404(self):
        self.assertEqual(self.post(self.outsider, name='X').status_code, 404)

    def test_created_by_cannot_be_forged(self):
        response = self.post(name='Planilhas', created_by=self.owner.pk)

        self.assertEqual(Folder.objects.get(pk=response.data['id']).created_by, self.member)


class RenameAndMoveTests(FolderAPITestCase):
    def patch(self, folder, user=None, **payload):
        return client_for(user or self.member).patch(folder_url(folder), payload, format='json')

    def test_rename(self):
        response = self.patch(self.atas, name='Atas de reunião')

        self.assertEqual(response.status_code, 200)
        self.atas.refresh_from_db()
        self.assertEqual(self.atas.name, 'Atas de reunião')

    def test_rename_to_sibling_name_is_rejected(self):
        response = self.patch(self.images, name='Documentos')

        self.assertEqual(response.status_code, 400)
        self.assertIn('name', response.data)

    def test_keeping_own_name_is_allowed(self):
        self.assertEqual(self.patch(self.images, name='Imagens').status_code, 200)

    def test_move_into_another_folder_and_back_to_root(self):
        self.assertEqual(self.patch(self.atas, parent=self.images.pk).status_code, 200)
        self.atas.refresh_from_db()
        self.assertEqual(self.atas.parent, self.images)

        self.assertEqual(self.patch(self.atas, parent=None).status_code, 200)
        self.atas.refresh_from_db()
        self.assertIsNone(self.atas.parent)

    def test_moving_keeps_subtree(self):
        self.patch(self.atas, parent=self.images.pk)

        self.year.refresh_from_db()
        self.assertEqual(self.year.parent, self.atas)

    def test_cannot_move_into_itself(self):
        response = self.patch(self.docs, parent=self.docs.pk)

        self.assertEqual(response.status_code, 400)
        self.assertIn('parent', response.data)

    def test_cannot_move_into_any_descendant(self):
        for descendant in (self.atas, self.year):
            with self.subTest(descendant=descendant.name):
                response = self.patch(self.docs, parent=descendant.pk)
                self.assertEqual(response.status_code, 400)
                self.assertIn('parent', response.data)
        self.docs.refresh_from_db()
        self.assertIsNone(self.docs.parent)

    def test_cannot_move_into_folder_of_another_project(self):
        response = self.patch(self.atas, parent=self.foreign.pk)

        self.assertEqual(response.status_code, 400)
        self.assertIn('parent', response.data)

    def test_moving_where_name_already_exists_is_rejected(self):
        self.make('Atas', self.images)

        response = self.patch(self.atas, parent=self.images.pk)

        self.assertEqual(response.status_code, 400)
        self.assertIn('name', response.data)

    def test_project_and_created_by_cannot_change(self):
        own_other = make_project(self.member, name='Meu outro')

        self.patch(self.images, created_by=self.member.pk)
        response = self.patch(self.images, project=own_other.pk)

        self.assertEqual(response.status_code, 400)
        self.images.refresh_from_db()
        self.assertEqual((self.images.project, self.images.created_by), (self.project, self.owner))

    def test_outsider_gets_404(self):
        self.assertEqual(self.patch(self.docs, self.outsider, name='X').status_code, 404)


class DeleteFolderTests(FolderAPITestCase):
    def test_member_deletes_folder_with_subtree(self):
        response = client_for(self.member).delete(folder_url(self.docs))

        self.assertEqual(response.status_code, 204)
        self.assertEqual(list(Folder.objects.filter(project=self.project)), [self.images])

    def test_outsider_gets_404(self):
        self.assertEqual(client_for(self.outsider).delete(folder_url(self.docs)).status_code, 404)
        self.assertTrue(Folder.objects.filter(pk=self.docs.pk).exists())
