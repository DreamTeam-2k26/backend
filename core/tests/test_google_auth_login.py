import time
from unittest import mock

from django.db import IntegrityError
from django.test import override_settings
from google.auth import exceptions as google_exceptions
from rest_framework.test import APIClient, APITestCase
from rest_framework_simplejwt.tokens import AccessToken

from core.models import GoogleIdentity, Group, User
from core.serializers.google_auth import GoogleLoginSerializer

from .google_helpers import ATTACKER_PRIVATE_KEY, GOOGLE_SUB, GoogleAuthMixin, google_credential
from .helpers import make_project, make_user

GOOGLE_LOGIN = '/api/auth/google/'


class GoogleLoginTestCase(GoogleAuthMixin, APITestCase):
    def login(self, credential=None, **claims):
        return APIClient().post(GOOGLE_LOGIN, {'credential': credential or google_credential(**claims)}, format='json')

    def assert_error(self, response, status, code):
        self.assertEqual(response.status_code, status, response.data)
        self.assertEqual(response.data['code'], code)
        self.assertIn('detail', response.data)
        self.assertNotIn('access', response.data)
        self.assertNotIn('refresh', response.data)


class NewUserTests(GoogleLoginTestCase):
    def test_new_user_receives_fluxo_jwt_and_can_call_me(self):
        response = self.login()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(set(response.data), {'access', 'refresh'})
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {response.data["access"]}')
        me = client.get('/api/users/me/')
        self.assertEqual(me.status_code, 200)
        self.assertEqual((me.data['email'], me.data['name']), ('ana.souza@gmail.com', 'Ana Souza'))

    def test_user_is_created_with_link_default_group_and_no_privileges(self):
        self.login(is_staff=True, is_superuser=True, groups=['admin'])

        user = User.objects.get(email='ana.souza@gmail.com')
        self.assertEqual(GoogleIdentity.objects.get(user=user).google_sub, GOOGLE_SUB)
        self.assertEqual(list(user.groups.all()), [Group.objects.get(is_default=True)])
        self.assertEqual((user.is_active, user.is_staff, user.is_superuser), (True, False, False))
        self.assertTrue(user.has_perm('uploader.add_image'))

    def test_access_token_is_fluxo_jwt_with_existing_claims(self):
        access = AccessToken(self.login().data['access'])
        user = User.objects.get(email='ana.souza@gmail.com')

        self.assertEqual(int(access['user_id']), user.pk)
        self.assertEqual(access['groups'], [Group.objects.get(is_default=True).slug])
        self.assertFalse(access['is_staff'])

    def test_google_account_has_no_usable_password(self):
        self.login()
        user = User.objects.get(email='ana.souza@gmail.com')

        self.assertFalse(user.has_usable_password())
        for password in ('', 'None', '!'):
            with self.subTest(password=password):
                response = APIClient().post('/api/token/', {'email': user.email, 'password': password})
                self.assertNotEqual(response.status_code, 200)

    def test_name_fallbacks(self):
        cases = [
            ('bia@gmail.com', {'name': None, 'given_name': 'Bia', 'family_name': 'Lima'}, 'Bia Lima'),
            ('carla.melo@gmail.com', {'name': '   '}, 'carla.melo'),
            ('longo@gmail.com', {'name': 'x' * 300}, 'x' * 150),
        ]
        for email, claims, expected in cases:
            with self.subTest(email=email):
                response = self.login(sub=f'sub-{email}', email=email, **claims)
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(User.objects.get(email=email).name, expected)

    def test_new_user_keeps_access_to_projects(self):
        access = self.login().data['access']
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {access}')

        created = client.post('/api/projects/', {'name': 'Feira'})

        self.assertEqual(created.status_code, 201)
        self.assertEqual(client.get('/api/projects/').data['results'][0]['role'], 'owner')


class LinkedUserTests(GoogleLoginTestCase):
    def test_linked_user_logs_in_again_without_duplicates(self):
        first = self.login()
        second = self.login()

        self.assertEqual(second.status_code, 200)
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(GoogleIdentity.objects.count(), 1)
        self.assertEqual(AccessToken(first.data['access'])['user_id'], AccessToken(second.data['access'])['user_id'])

    def test_sub_is_the_key_not_the_email(self):
        self.login()
        user = User.objects.get()

        response = self.login(email='ana.nova@gmail.com', name='Ana Nova')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(int(AccessToken(response.data['access'])['user_id']), user.pk)
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(GoogleIdentity.objects.get().email, 'ana.nova@gmail.com')
        user.refresh_from_db()
        self.assertEqual(user.email, 'ana.souza@gmail.com')

    def test_returning_user_keeps_projects(self):
        self.login()
        user = User.objects.get()
        make_project(user, name='Projeto da Ana')

        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.login().data["access"]}')

        self.assertEqual([p['name'] for p in client.get('/api/projects/').data['results']], ['Projeto da Ana'])

    def test_inactive_linked_user_cannot_log_in(self):
        self.login()
        User.objects.update(is_active=False)

        self.assert_error(self.login(), 403, 'inactive_account')


class EmailCollisionTests(GoogleLoginTestCase):
    def test_existing_email_is_not_taken_over(self):
        owner = make_user('ana.souza')
        owner.email = 'Ana.Souza@gmail.com'
        owner.save()

        response = self.login()

        self.assert_error(response, 409, 'account_link_required')
        self.assertEqual(User.objects.count(), 1)
        self.assertFalse(GoogleIdentity.objects.exists())
        owner.refresh_from_db()
        self.assertTrue(owner.check_password('senha-forte-123'))


class InvalidCredentialTests(GoogleLoginTestCase):
    def test_invalid_tokens_are_rejected_without_creating_users(self):
        now = int(time.time())
        cases = {
            'assinado por outra chave': google_credential(private_key=ATTACKER_PRIVATE_KEY),
            'expirado': google_credential(iat=now - 7200, exp=now - 3600),
            'de outro aplicativo (aud)': google_credential(aud='outro-app.apps.googleusercontent.com'),
            'emissor falso (iss)': google_credential(iss='https://accounts.google.com.evil.example'),
            'texto qualquer': 'nao-e-um-jwt',
            'sem sub': google_credential(sub=None),
        }
        for label, credential in cases.items():
            with self.subTest(label):
                self.assert_error(self.login(credential), 401, 'invalid_google_token')
        self.assertFalse(User.objects.exists())

    def test_unverified_or_missing_email_is_rejected(self):
        for claims in ({'email_verified': False}, {'email_verified': None}, {'email': None}):
            with self.subTest(claims=claims):
                self.assert_error(self.login(**claims), 403, 'google_email_not_verified')
        self.assertFalse(User.objects.exists())

    def test_credential_is_required(self):
        response = APIClient().post(GOOGLE_LOGIN, {}, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertIn('credential', response.data)

    def test_broken_bearer_header_does_not_block_public_login(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer token-velho-quebrado')

        self.assertEqual(client.post(GOOGLE_LOGIN, {'credential': google_credential()}, format='json').status_code, 200)


class ServiceAvailabilityTests(GoogleLoginTestCase):
    @override_settings(GOOGLE_CLIENT_ID='')
    def test_missing_configuration_returns_503(self):
        self.assert_error(self.login(), 503, 'google_auth_unavailable')
        self.assertFalse(User.objects.exists())

    def test_google_outage_returns_503_without_creating_user(self):
        self.fetch_certs.side_effect = google_exceptions.TransportError('falha de rede')

        self.assert_error(self.login(), 503, 'google_unavailable')
        self.assertFalse(User.objects.exists())

    def test_errors_never_echo_the_credential(self):
        credential = google_credential(aud='outro-app')

        response = self.login(credential)

        self.assertNotIn(credential, str(response.data))


class ConcurrencyTests(GoogleLoginTestCase):
    def test_concurrent_first_login_reuses_the_account_created_by_the_other_request(self):
        # Simula a corrida: esta requisição não viu o vínculo, mas outra já criou usuário + vínculo.
        other = User.objects.create_user(email='ana.souza@gmail.com', name='Ana Souza')
        identity = GoogleIdentity.objects.create(user=other, google_sub=GOOGLE_SUB, email=other.email)

        with mock.patch.object(GoogleLoginSerializer, 'find_identity', side_effect=[None, identity]):
            response = self.login()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(int(AccessToken(response.data['access'])['user_id']), other.pk)
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(GoogleIdentity.objects.count(), 1)

    def test_failure_while_linking_does_not_leave_orphan_user(self):
        with (
            mock.patch.object(GoogleIdentity.objects, 'create', side_effect=IntegrityError('duplicado')),
            mock.patch.object(GoogleLoginSerializer, 'find_identity', return_value=None),
        ):
            response = self.login()

        self.assertEqual(response.status_code, 409, response.data)
        self.assertFalse(User.objects.exists())


class TraditionalAuthRegressionTests(APITestCase):
    def test_register_login_refresh_and_me_still_work(self):
        register = APIClient().post(
            '/api/register/', {'email': 'novo@example.com', 'name': 'Novo', 'password': 'Senha-forte-123'}
        )
        self.assertEqual(register.status_code, 201, register.data)

        tokens = APIClient().post('/api/token/', {'email': 'novo@example.com', 'password': 'Senha-forte-123'})
        self.assertEqual(tokens.status_code, 200)

        refreshed = APIClient().post('/api/token/refresh/', {'refresh': tokens.data['refresh']})
        self.assertEqual(refreshed.status_code, 200)

        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {refreshed.data["access"]}')
        self.assertEqual(client.get('/api/users/me/').data['email'], 'novo@example.com')
