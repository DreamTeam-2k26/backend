import uuid

from django.urls import reverse
from rest_framework.test import APITestCase

from core.models import User
from uploader.models import Image


class UserProfileTests(APITestCase):
    password = 'Safe-profile-pass-2026!'

    def setUp(self):
        self.user = User.objects.create_user(email='profile@example.com', password=self.password)

    def authenticate(self):
        response = self.client.post(
            reverse('token_obtain_pair'),
            {
                'email': self.user.email,
                'password': self.password,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {response.data["access"]}')
        return response.data

    def test_register_with_name_and_optional_picture(self):
        for index, extra in enumerate(({'name': 'Ana'}, {'name': 'Maria', 'profile_picture_attachment_key': None})):
            with self.subTest(extra=extra):
                response = self.client.post(
                    reverse('user_registration'),
                    {
                        'email': f'new{index}@example.com',
                        'password': self.password,
                        **extra,
                        'is_staff': True,
                        'is_superuser': True,
                    },
                    format='json',
                )
                self.assertEqual(response.status_code, 201)
                user = User.objects.get(pk=response.data['id'])
                self.assertEqual(user.name, extra.get('name', ''))
                self.assertTrue(user.check_password(self.password))
                self.assertIsNone(response.data['profile_picture'])
                self.assertFalse(user.is_staff)
                self.assertFalse(user.is_superuser)
                self.assertNotIn('password', response.data)
                self.assertNotIn('is_staff', response.data)

    def test_jwt_and_profile_update_without_reauthentication(self):
        tokens = self.authenticate()
        response = self.client.get(reverse('users-me'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data), {'id', 'email', 'name', 'profile_picture'})
        response = self.client.patch(
            reverse('users-me'),
            {
                'name': 'Nome atualizado',
                'is_staff': True,
                'is_active': False,
                'profile_picture_attachment_key': None,
            },
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['name'], 'Nome atualizado')
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_staff)
        self.assertTrue(self.user.is_active)
        self.assertEqual(self.client.get(reverse('users-detail', args=[self.user.pk])).data['name'], self.user.name)
        self.assertEqual(self.client.post(reverse('token_verify'), {'token': tokens['access']}).status_code, 200)
        self.assertEqual(self.client.post(reverse('token_refresh'), {'refresh': tokens['refresh']}).status_code, 200)

    def test_picture_must_exist_and_belong_to_user_and_can_be_removed(self):
        other = User.objects.create_user(email='other@example.com', password=self.password)
        own_image = Image.objects.create(file='images/own.png', uploaded_by=self.user)
        other_image = Image.objects.create(file='images/other.png', uploaded_by=other)
        self.authenticate()
        for key in (str(uuid.uuid4()), str(other_image.attachment_key), 'invalid'):
            with self.subTest(key=key):
                response = self.client.patch(reverse('users-me'), {'profile_picture_attachment_key': key})
                self.assertEqual(response.status_code, 400)
        response = self.client.patch(
            reverse('users-me'),
            {
                'profile_picture_attachment_key': str(own_image.attachment_key),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('url', response.data['profile_picture'])
        self.user.refresh_from_db()
        self.assertEqual(self.user.profile_picture_id, own_image.pk)
        response = self.client.patch(reverse('users-me'), {'profile_picture_attachment_key': None}, format='json')
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertIsNone(self.user.profile_picture)
        self.assertTrue(Image.objects.filter(pk=own_image.pk).exists())

    def test_name_length_and_own_profile_access(self):
        self.assertEqual(self.client.get(reverse('users-me')).status_code, 401)
        self.authenticate()
        response = self.client.patch(reverse('users-detail', args=[self.user.pk]), {'name': 'a' * 150})
        self.assertEqual(response.status_code, 200)
        response = self.client.patch(reverse('users-me'), {'name': 'a' * 151})
        self.assertEqual(response.status_code, 400)
        other = User.objects.create_user(email='other@example.com')
        response = self.client.patch(reverse('users-detail', args=[other.pk]), {'name': 'Invadido'})
        self.assertEqual(response.status_code, 403)

    def test_registration_rejects_invalid_picture_and_long_name(self):
        for extra in (
            {'name': 'a' * 151},
            {'name': ''},
            {'name': '   '},
            {'name': None},
            {'name': 'Ana', 'profile_picture_attachment_key': str(uuid.uuid4())},
            {},
        ):
            with self.subTest(extra=extra):
                response = self.client.post(
                    reverse('user_registration'),
                    {
                        'email': 'invalid@example.com',
                        'password': self.password,
                        **extra,
                    },
                    format='json',
                )
                self.assertEqual(response.status_code, 400)
