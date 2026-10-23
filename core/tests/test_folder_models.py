from django.test import TestCase

from core.models import Folder

from .helpers import make_project, make_user


class FolderModelTests(TestCase):
    def setUp(self):
        self.owner = make_user('dono')
        self.project = make_project(self.owner)

    def make(self, name, parent=None, **extra):
        return Folder.objects.create(project=self.project, parent=parent, name=name, **extra)

    def test_str_and_root(self):
        folder = self.make('Documentos')

        self.assertEqual(str(folder), 'Documentos')
        self.assertIsNone(folder.parent)

    def test_subfolders(self):
        docs = self.make('Documentos')
        child = self.make('Atas', docs)

        self.assertEqual(list(docs.children.all()), [child])

    def test_deleting_folder_deletes_whole_subtree(self):
        docs = self.make('Documentos')
        atas = self.make('Atas', docs)
        self.make('2026', atas)
        other = self.make('Imagens')

        docs.delete()

        self.assertEqual(list(Folder.objects.all()), [other])

    def test_deleting_project_deletes_folders(self):
        self.make('Atas', self.make('Documentos'))

        self.project.delete()

        self.assertFalse(Folder.objects.exists())

    def test_path_from_root(self):
        docs = self.make('Documentos')
        atas = self.make('Atas', docs)

        self.assertEqual(atas.ancestors_and_self(), [docs, atas])

    def test_path_stops_on_corrupted_cycle(self):
        # Ciclo que só entraria por fora da API (admin, corrida entre dois movimentos): não pode travar.
        docs = self.make('Documentos')
        atas = self.make('Atas', docs)
        Folder.objects.filter(pk=docs.pk).update(parent=atas)
        atas.refresh_from_db()

        self.assertEqual({folder.pk for folder in atas.ancestors_and_self()}, {docs.pk, atas.pk})

    def test_deleting_creator_keeps_folder(self):
        folder = self.make('Documentos', created_by=self.owner)

        self.owner.delete()
        folder.refresh_from_db()

        self.assertIsNone(folder.created_by)
