from rest_framework.test import APIClient, APITestCase

from core.models import SupportLink, Task

from .helpers import client_for, make_project, make_user

LINKS = '/api/support-links/'
URL = 'https://docs.google.com/document/d/abc'


def link_url(link):
    return f'{LINKS}{link.pk}/'


class SupportLinkAPITestCase(APITestCase):
    def setUp(self):
        self.owner = make_user('dono')
        self.member = make_user('membro')
        self.outsider = make_user('externo')
        self.project = make_project(self.owner, members=[self.member])
        self.other_project = make_project(self.outsider, name='Outro projeto')
        self.task = Task.objects.create(project=self.project, column=self.project.columns.first(), title='Cartaz')
        self.other_task = Task.objects.create(
            project=self.other_project, column=self.other_project.columns.first(), title='Alheia'
        )
        self.general = SupportLink.objects.create(project=self.project, title='Edital', url=URL, created_by=self.owner)
        self.of_task = SupportLink.objects.create(
            project=self.project, task=self.task, title='Modelo do cartaz', url=URL, created_by=self.owner
        )

    def titles(self, response):
        return {item['title'] for item in response.data['results']}


class AuthenticationTests(SupportLinkAPITestCase):
    def test_endpoints_require_authentication(self):
        anonymous = APIClient()
        url = link_url(self.general)
        for method, path in (('get', LINKS), ('post', LINKS), ('get', url), ('patch', url), ('delete', url)):
            with self.subTest(method=method, path=path):
                self.assertEqual(getattr(anonymous, method)(path).status_code, 401)


class CreateLinkTests(SupportLinkAPITestCase):
    def post(self, user=None, **payload):
        data = {'project': self.project.pk, 'title': 'Planilha', 'url': URL, **payload}
        return client_for(user or self.member).post(LINKS, data)

    def test_member_creates_general_project_link(self):
        response = self.post()

        self.assertEqual(response.status_code, 201)
        link = SupportLink.objects.get(pk=response.data['id'])
        self.assertEqual((link.project, link.task, link.created_by), (self.project, None, self.member))

    def test_member_creates_task_link(self):
        response = self.post(task=self.task.pk)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(SupportLink.objects.get(pk=response.data['id']).task, self.task)

    def test_task_of_another_project_is_rejected(self):
        response = self.post(task=self.other_task.pk)

        self.assertEqual(response.status_code, 400)
        self.assertIn('task', response.data)
        self.assertFalse(SupportLink.objects.filter(task=self.other_task).exists())

    def test_outsider_gets_404(self):
        self.assertEqual(self.post(self.outsider).status_code, 404)

    def test_url_must_be_http_or_https(self):
        for url in ('nao-e-link', 'javascript:alert(1)', 'ftp://exemplo.com/arquivo', ''):
            with self.subTest(url=url):
                response = self.post(url=url)
                self.assertEqual(response.status_code, 400)
                self.assertIn('url', response.data)

    def test_title_is_required(self):
        response = self.post(title='  ')

        self.assertEqual(response.status_code, 400)
        self.assertIn('title', response.data)

    def test_created_by_cannot_be_forged(self):
        response = self.post(created_by=self.owner.pk)

        self.assertEqual(SupportLink.objects.get(pk=response.data['id']).created_by, self.member)


class ListAndDetailTests(SupportLinkAPITestCase):
    def test_member_lists_project_links(self):
        response = client_for(self.member).get(LINKS, {'project': self.project.pk})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.titles(response), {'Edital', 'Modelo do cartaz'})

    def test_filter_task_links_and_general_links(self):
        client = client_for(self.member)

        self.assertEqual(self.titles(client.get(LINKS, {'task': self.task.pk})), {'Modelo do cartaz'})
        self.assertEqual(
            self.titles(client.get(LINKS, {'project': self.project.pk, 'task__isnull': 'true'})), {'Edital'}
        )

    def test_links_of_other_projects_are_isolated(self):
        SupportLink.objects.create(project=self.other_project, title='Alheio', url=URL)

        self.assertEqual(self.titles(client_for(self.member).get(LINKS)), {'Edital', 'Modelo do cartaz'})
        self.assertEqual(client_for(self.member).get(LINKS, {'project': self.other_project.pk}).data['results'], [])

    def test_member_sees_detail(self):
        response = client_for(self.member).get(link_url(self.of_task))

        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.data['url'], response.data['task']), (URL, self.task.pk))

    def test_outsider_gets_404(self):
        self.assertEqual(client_for(self.outsider).get(link_url(self.general)).status_code, 404)


class UpdateLinkTests(SupportLinkAPITestCase):
    def patch(self, link, user=None, **payload):
        return client_for(user or self.member).patch(link_url(link), payload, format='json')

    def test_member_edits_title_and_url(self):
        response = self.patch(self.general, title='Edital 2026', url='https://exemplo.com/novo')

        self.assertEqual(response.status_code, 200)
        self.general.refresh_from_db()
        self.assertEqual((self.general.title, self.general.url), ('Edital 2026', 'https://exemplo.com/novo'))

    def test_general_link_can_be_attached_to_task_and_detached(self):
        self.patch(self.general, task=self.task.pk)
        self.general.refresh_from_db()
        self.assertEqual(self.general.task, self.task)

        self.patch(self.general, task=None)
        self.general.refresh_from_db()
        self.assertIsNone(self.general.task)

    def test_cannot_attach_task_of_another_project(self):
        response = self.patch(self.general, task=self.other_task.pk)

        self.assertEqual(response.status_code, 400)
        self.assertIn('task', response.data)
        self.general.refresh_from_db()
        self.assertIsNone(self.general.task)

    def test_project_cannot_change(self):
        own_other = make_project(self.member, name='Meu outro')

        response = self.patch(self.general, project=own_other.pk)

        self.assertEqual(response.status_code, 400)
        self.general.refresh_from_db()
        self.assertEqual(self.general.project, self.project)

    def test_created_by_cannot_be_changed(self):
        self.patch(self.general, created_by=self.member.pk, title='Edital editado')

        self.general.refresh_from_db()
        self.assertEqual((self.general.created_by, self.general.title), (self.owner, 'Edital editado'))

    def test_invalid_url_is_rejected(self):
        response = self.patch(self.general, url='javascript:alert(1)')

        self.assertEqual(response.status_code, 400)
        self.assertIn('url', response.data)

    def test_outsider_gets_404(self):
        self.assertEqual(self.patch(self.general, self.outsider, title='X').status_code, 404)


class DeleteLinkTests(SupportLinkAPITestCase):
    def test_member_deletes_link(self):
        response = client_for(self.member).delete(link_url(self.general))

        self.assertEqual(response.status_code, 204)
        self.assertFalse(SupportLink.objects.filter(pk=self.general.pk).exists())

    def test_outsider_gets_404(self):
        self.assertEqual(client_for(self.outsider).delete(link_url(self.general)).status_code, 404)
        self.assertTrue(SupportLink.objects.filter(pk=self.general.pk).exists())
