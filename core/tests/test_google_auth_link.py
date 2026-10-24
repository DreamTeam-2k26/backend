from django.core.management import call_command
from django.test import SimpleTestCase, override_settings
from rest_framework.test import APIClient, APITestCase
from rest_framework_simplejwt.tokens import AccessToken

from core.checks import check_google_client_id
from core.models import GoogleIdentity, User

from .google_helpers import GOOGLE_SUB, GoogleAuthMixin, google_credential
from .helpers import make_project, make_user

GOOGLE_LOGIN = '/api/auth/google/'
GOOGLE_LINK = '/api/auth/google/link/'


def password_client(user, password='senha-forte-123'):
    """Login tradicional (e-mail + senha) e cliente com o JWT do Fluxo."""
    tokens = APIClient().post('/api/token/', {'email': user.email, 'password': password})
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Bearer {tokens.data["access"]}')
    return client


class GoogleLinkTests(GoogleAuthMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.ana = make_user('ana.souza')
        self.ana.email = 'ana.souza@gmail.com'
        self.ana.save()
        self.client_ana = password_client(self.ana)

    def link(self, client=None, **claims):
        return (client or self.client_ana).post(GOOGLE_LINK, {'credential': google_credential(**claims)}, format='json')

    def test_requires_fluxo_jwt(self):
        self.assertEqual(APIClient().post(GOOGLE_LINK, {'credential': google_credential()}).status_code, 401)
        self.assertEqual(APIClient().get(GOOGLE_LINK).status_code, 401)

    def test_full_flow_password_login_then_link_then_google_login(self):
        # 1) Google sozinho não toma a conta que já existe com o mesmo e-mail.
        blocked = APIClient().post(GOOGLE_LOGIN, {'credential': google_credential()}, format='json')
        self.assertEqual((blocked.status_code, blocked.data['code']), (409, 'account_link_required'))

        # 2) Logada com senha, a Ana vincula a conta Google.
        linked = self.link()
        self.assertEqual(linked.status_code, 200, linked.data)
        self.assertEqual(linked.data, {'linked': True, 'email': 'ana.souza@gmail.com'})

        # 3) Agora o Google entra na MESMA conta, com os projetos dela.
        make_project(self.ana, name='Projeto da Ana')
        response = APIClient().post(GOOGLE_LOGIN, {'credential': google_credential()}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(int(AccessToken(response.data['access'])['user_id']), self.ana.pk)
        self.assertEqual(User.objects.count(), 1)

        # 4) A senha continua funcionando.
        self.assertEqual(
            APIClient().post('/api/token/', {'email': self.ana.email, 'password': 'senha-forte-123'}).status_code, 200
        )

    def test_google_email_may_differ_from_fluxo_email(self):
        self.assertEqual(self.link(email='ana.pessoal@gmail.com').status_code, 200)

        self.assertEqual(GoogleIdentity.objects.get(user=self.ana).email, 'ana.pessoal@gmail.com')

    def test_linking_same_google_again_is_idempotent(self):
        self.link()

        self.assertEqual(self.link().status_code, 200)
        self.assertEqual(GoogleIdentity.objects.count(), 1)

    def test_cannot_steal_google_account_linked_to_someone_else(self):
        bia = make_user('bia')
        GoogleIdentity.objects.create(user=bia, google_sub=GOOGLE_SUB, email='ana.souza@gmail.com')

        response = self.link()

        self.assertEqual((response.status_code, response.data['code']), (409, 'google_account_in_use'))
        self.assertEqual(GoogleIdentity.objects.get(google_sub=GOOGLE_SUB).user, bia)

    def test_user_already_linked_to_another_google_account(self):
        self.link()

        response = self.link(sub='outro-sub-do-google', email='ana.outra@gmail.com')

        self.assertEqual((response.status_code, response.data['code']), (409, 'already_linked'))
        self.assertEqual(GoogleIdentity.objects.get(user=self.ana).google_sub, GOOGLE_SUB)

    def test_invalid_or_unverified_google_token_does_not_link(self):
        cases = [
            ({'aud': 'outro-app'}, 401, 'invalid_google_token'),
            ({'email_verified': False}, 403, 'google_email_not_verified'),
        ]
        for claims, status, code in cases:
            with self.subTest(code=code):
                response = self.link(**claims)
                self.assertEqual((response.status_code, response.data['code']), (status, code))
        self.assertFalse(GoogleIdentity.objects.exists())

    def test_link_does_not_change_profile_or_privileges(self):
        self.link(name='Outro Nome', is_staff=True)

        self.ana.refresh_from_db()
        self.assertEqual((self.ana.name, self.ana.email, self.ana.is_staff), ('', 'ana.souza@gmail.com', False))

    def test_link_status(self):
        self.assertEqual(self.client_ana.get(GOOGLE_LINK).data, {'linked': False, 'email': None})

        self.link()

        self.assertEqual(self.client_ana.get(GOOGLE_LINK).data, {'linked': True, 'email': 'ana.souza@gmail.com'})

    @override_settings(GOOGLE_CLIENT_ID='')
    def test_missing_configuration_returns_503(self):
        response = self.link()

        self.assertEqual((response.status_code, response.data['code']), (503, 'google_auth_unavailable'))


class GoogleConfigurationCheckTests(SimpleTestCase):
    @override_settings(GOOGLE_CLIENT_ID='', DEBUG=False)
    def test_warns_in_production_without_client_id(self):
        warnings = check_google_client_id(None)

        self.assertEqual([warning.id for warning in warnings], ['core.W001'])

    @override_settings(GOOGLE_CLIENT_ID='abc.apps.googleusercontent.com', DEBUG=False)
    def test_no_warning_when_configured(self):
        self.assertEqual(check_google_client_id(None), [])

    @override_settings(GOOGLE_CLIENT_ID='', DEBUG=True)
    def test_no_warning_in_development(self):
        self.assertEqual(check_google_client_id(None), [])

    def test_check_is_registered(self):
        call_command('check', verbosity=0)
