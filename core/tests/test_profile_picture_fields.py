from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APITestCase

from chat.tests.helpers import JPEG_BYTES, TempMediaMixin
from core.models import Column, Task
from uploader.models import Image

from .helpers import client_for, make_project, make_user

MEMBERSHIPS = '/api/memberships/'
TASKS = '/api/tasks/'
FILES = '/api/files/'


def with_picture(user):
    user.profile_picture = Image.objects.create(
        file=SimpleUploadedFile('foto.jpg', JPEG_BYTES, content_type='image/jpeg'), uploaded_by=user
    )
    user.save()
    return user


class ProfilePictureFieldsTests(TempMediaMixin, APITestCase):
    """Fotos de perfil nas listas que o frontend mostra com avatar (URL ou null, como no comentário)."""

    def setUp(self):
        self.owner = with_picture(make_user('dono-foto'))
        self.member = make_user('membro-sem-foto')
        self.project = make_project(self.owner, members=[self.member])
        self.column = Column.objects.filter(project=self.project).first()

    def test_membership_list_returns_user_picture(self):
        response = client_for(self.member).get(MEMBERSHIPS, {'project': self.project.pk})

        pictures = {item['user']: item['user_profile_picture'] for item in response.data['results']}
        self.assertEqual(pictures, {self.owner.pk: self.owner.profile_picture.url, self.member.pk: None})

    def test_membership_detail_returns_user_picture(self):
        membership = self.project.memberships.get(user=self.owner)

        response = client_for(self.member).get(f'{MEMBERSHIPS}{membership.pk}/')

        self.assertEqual(response.data['user_profile_picture'], self.owner.profile_picture.url)

    def test_task_list_returns_assignee_picture(self):
        Task.objects.create(project=self.project, column=self.column, title='Com foto', assignee=self.owner)
        Task.objects.create(project=self.project, column=self.column, title='Sem responsável')

        response = client_for(self.member).get(TASKS, {'project': self.project.pk})

        pictures = {item['title']: item['assignee_profile_picture'] for item in response.data['results']}
        self.assertEqual(pictures, {'Com foto': self.owner.profile_picture.url, 'Sem responsável': None})

    def test_task_detail_returns_assignee_and_creator_pictures(self):
        task = Task.objects.create(
            project=self.project, column=self.column, title='Tarefa', assignee=self.member, created_by=self.owner
        )

        response = client_for(self.member).get(f'{TASKS}{task.pk}/')

        self.assertIsNone(response.data['assignee_profile_picture'])
        self.assertEqual(response.data['created_by_profile_picture'], self.owner.profile_picture.url)

    def test_file_list_returns_uploader_picture(self):
        upload = SimpleUploadedFile('edital.pdf', b'%PDF-1.4\n%%EOF\n', content_type='application/pdf')
        created = client_for(self.owner).post(FILES, {'project': self.project.pk, 'file': upload}, format='multipart')
        self.assertEqual(created.status_code, 201, created.data)

        response = client_for(self.member).get(FILES, {'project': self.project.pk})

        self.assertEqual(response.data['results'][0]['uploaded_by_profile_picture'], self.owner.profile_picture.url)


class ProfilePictureQueryCountTests(TempMediaMixin, APITestCase):
    """A foto vem junto da consulta da lista: mais linhas com foto não somam consultas."""

    def setUp(self):
        self.owner = with_picture(make_user('dono-consultas'))
        self.project = make_project(self.owner)
        self.column = Column.objects.filter(project=self.project).first()

    def add_member_with_task(self, index):
        user = with_picture(make_user(f'pessoa-{index}'))
        self.project.memberships.create(user=user, role='member')
        Task.objects.create(project=self.project, column=self.column, title=f'T{index}', assignee=user, created_by=user)
        upload = SimpleUploadedFile(f'f{index}.pdf', b'%PDF-1.4\n%%EOF\n', content_type='application/pdf')
        client_for(user).post(FILES, {'project': self.project.pk, 'file': upload}, format='multipart')

    def count(self, url):
        client = client_for(self.owner)
        with CaptureQueriesContext(connection) as queries:
            response = client.get(url, {'project': self.project.pk})
        self.assertEqual(response.status_code, 200)
        return len(queries)

    def test_lists_do_not_query_once_per_picture(self):
        self.add_member_with_task(1)
        before = {url: self.count(url) for url in (MEMBERSHIPS, TASKS, FILES)}
        for index in range(2, 5):
            self.add_member_with_task(index)
        after = {url: self.count(url) for url in (MEMBERSHIPS, TASKS, FILES)}
        self.assertEqual(after, before)
