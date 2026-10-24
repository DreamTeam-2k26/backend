from importlib import import_module
from io import BytesIO

from django.apps import apps
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from PIL import Image as PillowImage
from rest_framework.test import APIClient, APITestCase

from chat.tests.helpers import TempMediaMixin
from core.models import Group, User

from .helpers import client_for

default_group_migration = import_module('core.migrations.0007_default_group_baseline_permissions')


def png_bytes():
    buffer = BytesIO()
    PillowImage.new('RGB', (1, 1)).save(buffer, format='PNG')
    return buffer.getvalue()


class DefaultGroupMigrationTests(TestCase):
    def test_default_group_exists_with_image_upload_permission(self):
        group = Group.objects.get(is_default=True)

        self.assertTrue(group.permissions.filter(content_type__app_label='uploader', codename='add_image').exists())

    def test_users_without_group_join_default_group(self):
        loose = User.objects.create_user(email='solto@example.com', password='senha-forte-123')
        loose.groups.clear()
        other_group = Group.objects.create(name='Professores', slug='professores')
        grouped = User.objects.create_user(email='agrupado@example.com', password='senha-forte-123')
        grouped.groups.set([other_group])

        default_group_migration.ensure_default_group(apps, None)

        default = Group.objects.get(is_default=True)
        self.assertEqual(list(loose.groups.all()), [default])
        self.assertEqual(list(grouped.groups.all()), [other_group])

    def test_existing_default_group_is_reused(self):
        Group.objects.filter(is_default=True).update(name='Usuarios', slug='usuarios')

        default_group_migration.ensure_default_group(apps, None)

        self.assertEqual(Group.objects.filter(is_default=True).count(), 1)
        self.assertEqual(Group.objects.get(is_default=True).name, 'Usuarios')


class NewUserUploadTests(TempMediaMixin, APITestCase):
    def test_registered_user_can_upload_profile_picture(self):
        response = APIClient().post(
            '/api/register/', {'email': 'novo@example.com', 'name': 'Novo', 'password': 'Senha-forte-123'}
        )
        self.assertEqual(response.status_code, 201, response.data)
        user = User.objects.get(email='novo@example.com')

        upload = client_for(user).post(
            '/api/media/images/',
            {'file': SimpleUploadedFile('foto.png', png_bytes(), content_type='image/png')},
            format='multipart',
        )

        self.assertEqual(upload.status_code, 201, upload.data)
