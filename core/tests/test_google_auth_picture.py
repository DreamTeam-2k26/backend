"""Foto de perfil vinda da conta Google (claim `picture` do ID token), sem acessar a internet nos testes."""

import io
from unittest import mock

import requests
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image as PillowImage
from rest_framework.test import APIClient, APITestCase

from chat.tests.helpers import HTML_BYTES, TempMediaMixin
from core.models import GoogleIdentity, User
from uploader.models import Image

from .google_helpers import GoogleAuthMixin, google_credential
from .test_google_auth_link import password_client

GOOGLE_LOGIN = '/api/auth/google/'
GOOGLE_LINK = '/api/auth/google/link/'
PHOTO_URL = 'https://lh3.googleusercontent.com/a/ACg8ocFoto=s96-c'
NEW_PHOTO_URL = 'https://lh3.googleusercontent.com/a/ACg8ocNova=s96-c'


def image_bytes(fmt='JPEG', color=(200, 30, 30)):
    buffer = io.BytesIO()
    PillowImage.new('RGB', (4, 4), color).save(buffer, format=fmt)
    return buffer.getvalue()


class FakeResponse:
    def __init__(self, content=b'', status_code=200):
        self.content = content
        self.status_code = status_code

    def iter_content(self, chunk_size):
        for start in range(0, len(self.content), chunk_size):
            yield self.content[start : start + chunk_size]

    def close(self):
        pass


class GooglePictureTestCase(GoogleAuthMixin, TempMediaMixin, APITestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch('core.serializers.google_auth._picture_session.get')
        self.http_get = patcher.start()
        self.addCleanup(patcher.stop)
        self.http_get.return_value = FakeResponse(image_bytes())

    def google_login(self, **claims):
        claims.setdefault('picture', PHOTO_URL)
        response = APIClient().post(GOOGLE_LOGIN, {'credential': google_credential(**claims)}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        return response

    def user(self):
        return User.objects.select_related('profile_picture').get(email='ana.souza@gmail.com')


class NewGoogleUserTests(GooglePictureTestCase):
    def test_new_user_gets_google_photo_everywhere_profile_pictures_appear(self):
        access = self.google_login().data['access']

        user = self.user()
        self.assertIsNotNone(user.profile_picture)
        self.assertEqual(user.profile_picture.uploaded_by, user)
        self.assertEqual(user.profile_picture.file.read(), image_bytes())
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {access}')
        self.assertEqual(client.get('/api/users/me/').data['profile_picture']['url'], user.profile_picture.url)
        self.assertEqual(GoogleIdentity.objects.get().picture_url, PHOTO_URL)

    def test_download_is_safe(self):
        self.google_login()

        url = self.http_get.call_args.args[0]
        kwargs = self.http_get.call_args.kwargs
        self.assertEqual(url, 'https://lh3.googleusercontent.com/a/ACg8ocFoto=s256-c')
        self.assertFalse(kwargs['allow_redirects'])
        self.assertTrue(kwargs['stream'])
        self.assertIn('timeout', kwargs)

    def test_png_is_accepted(self):
        self.http_get.return_value = FakeResponse(image_bytes('PNG'))

        self.google_login()

        self.assertIsNotNone(self.user().profile_picture)

    def test_without_picture_claim_nothing_is_downloaded(self):
        self.google_login(picture=None)

        self.http_get.assert_not_called()
        self.assertIsNone(self.user().profile_picture)

    def test_only_https_google_hosts_are_fetched(self):
        urls = [
            'https://evil.example/foto.jpg',
            'http://lh3.googleusercontent.com/a/x',
            'https://googleusercontent.com.evil.example/a/x',
            'https://169.254.169.254/latest/meta-data',
            'nao-e-url',
        ]
        for index, url in enumerate(urls):
            with self.subTest(url=url):
                self.google_login(sub=f'sub-{index}', email=f'pessoa{index}@gmail.com', picture=url)
        self.http_get.assert_not_called()
        self.assertFalse(Image.objects.exists())

    def test_failed_download_never_blocks_login(self):
        # Respostas ruins são recusadas de forma limpa (sem registrar erro), e o login segue sem foto.
        failures = [
            FakeResponse(image_bytes(), status_code=404),
            FakeResponse(image_bytes(), status_code=302),
            FakeResponse(HTML_BYTES),
            FakeResponse(b''),
            FakeResponse(image_bytes('GIF')),
        ]
        with self.assertNoLogs('core.serializers.google_auth', level='ERROR'):
            for index, failure in enumerate(failures):
                with self.subTest(failure=index):
                    self.http_get.return_value = failure
                    self.google_login(sub=f'sub-falha-{index}', email=f'falha{index}@gmail.com')
            for index, error in enumerate((requests.ConnectionError('rede caiu'), OSError('socket'))):
                self.http_get.side_effect = error
                self.google_login(sub=f'sub-rede-{index}', email=f'rede{index}@gmail.com')

        self.assertFalse(Image.objects.exists())
        self.assertEqual(User.objects.filter(profile_picture__isnull=False).count(), 0)

    def test_storage_failure_never_blocks_login(self):
        with (
            mock.patch.object(Image.objects, 'create', side_effect=OSError('storage fora do ar')),
            self.assertLogs('core.serializers.google_auth', level='ERROR'),
        ):
            self.google_login()

        self.assertIsNone(self.user().profile_picture)
        self.assertEqual(GoogleIdentity.objects.get().picture_url, '')

    def test_too_large_photo_is_ignored(self):
        with self.settings(MAX_UPLOAD_SIZE=10):
            self.google_login()

        self.assertIsNone(self.user().profile_picture)


class ExistingPhotoTests(GooglePictureTestCase):
    def setUp(self):
        super().setUp()
        self.ana = User.objects.create_user(email='ana.souza@gmail.com', password='senha-forte-123', name='Ana')
        self.own = Image.objects.create(
            file=SimpleUploadedFile('minha.jpg', image_bytes(color=(0, 0, 255))), uploaded_by=self.ana
        )

    def link(self, **claims):
        claims.setdefault('picture', PHOTO_URL)
        response = password_client(self.ana).post(GOOGLE_LINK, {'credential': google_credential(**claims)})
        self.assertEqual(response.status_code, 200, response.data)

    def test_linking_account_without_photo_brings_google_photo(self):
        self.link()

        self.assertEqual(self.user().profile_picture.file.read(), image_bytes())

    def test_own_photo_is_never_replaced(self):
        self.ana.profile_picture = self.own
        self.ana.save()

        self.link()
        self.google_login(picture=NEW_PHOTO_URL)

        self.http_get.assert_not_called()
        self.assertEqual(self.user().profile_picture, self.own)


class RefreshTests(GooglePictureTestCase):
    def setUp(self):
        super().setUp()
        self.google_login()
        self.first = self.user().profile_picture
        self.http_get.reset_mock()

    def test_same_google_photo_is_not_downloaded_again(self):
        self.google_login()

        self.http_get.assert_not_called()
        self.assertEqual(self.user().profile_picture, self.first)

    def test_new_google_photo_replaces_the_previous_google_photo(self):
        self.http_get.return_value = FakeResponse(image_bytes(color=(0, 200, 0)))

        self.google_login(picture=NEW_PHOTO_URL)

        user = self.user()
        self.assertNotEqual(user.profile_picture, self.first)
        self.assertEqual(user.profile_picture.file.read(), image_bytes(color=(0, 200, 0)))
        self.assertEqual(GoogleIdentity.objects.get().picture_url, NEW_PHOTO_URL)
        self.assertFalse(Image.objects.filter(pk=self.first.pk).exists())

    def test_photo_chosen_in_fluxo_after_google_is_kept(self):
        user = self.user()
        own = Image.objects.create(file=SimpleUploadedFile('minha.jpg', image_bytes(color=(9, 9, 9))), uploaded_by=user)
        user.profile_picture = own
        user.save()

        self.google_login(picture=NEW_PHOTO_URL)

        self.http_get.assert_not_called()
        self.assertEqual(self.user().profile_picture, own)

    def test_removed_google_photo_does_not_come_back_unless_it_changes(self):
        User.objects.filter(pk=self.user().pk).update(profile_picture=None)

        self.google_login()
        self.http_get.assert_not_called()
        self.assertIsNone(self.user().profile_picture)

        self.google_login(picture=NEW_PHOTO_URL)
        self.assertIsNotNone(self.user().profile_picture)
