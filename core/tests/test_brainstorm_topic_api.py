from rest_framework.test import APIClient, APITestCase

from core.models import BrainstormReply, BrainstormTopic, BrainstormVote, Membership

from .helpers import client_for, make_project, make_user

TOPICS = '/api/brainstorm-topics/'


def topic_url(topic, action=''):
    return f'{TOPICS}{topic.pk}/{action + "/" if action else ""}'


class BrainstormTopicAPITestCase(APITestCase):
    def setUp(self):
        self.owner = make_user('dona')
        self.admin = make_user('admin')
        self.member = make_user('membro')
        self.outsider = make_user('externo')
        self.project = make_project(self.owner, admins=[self.admin], members=[self.member])
        self.other_project = make_project(self.outsider, name='Outro projeto')
        self.topic = BrainstormTopic.objects.create(
            project=self.project, author=self.member, title='Tema da feira', context='Escolher o tema.'
        )

    def post_topic(self, user=None, **payload):
        data = {'project': self.project.pk, 'title': 'Nova ideia', 'context': 'Contexto', **payload}
        return client_for(user or self.member).post(TOPICS, data)

    def ids(self, response):
        return [item['id'] for item in response.data['results']]


class AccessTests(BrainstormTopicAPITestCase):
    def test_requires_authentication(self):
        anonymous = APIClient()
        for method, path in (('get', TOPICS), ('post', TOPICS), ('get', topic_url(self.topic))):
            with self.subTest(method=method, path=path):
                self.assertEqual(getattr(anonymous, method)(path).status_code, 401)

    def test_outsider_gets_404(self):
        client = client_for(self.outsider)
        self.assertEqual(self.ids(client.get(TOPICS, {'project': self.project.pk})), [])
        self.assertEqual(client.get(topic_url(self.topic)).status_code, 404)
        self.assertEqual(client.patch(topic_url(self.topic), {'title': 'x'}).status_code, 404)
        self.assertEqual(client.post(topic_url(self.topic, 'close')).status_code, 404)
        self.assertEqual(self.post_topic(self.outsider).status_code, 404)

    def test_removed_member_loses_access(self):
        Membership.objects.filter(project=self.project, user=self.member).delete()
        client = client_for(self.member)
        self.assertEqual(client.get(topic_url(self.topic)).status_code, 404)
        self.assertEqual(self.post_topic().status_code, 404)


class CreateAndListTests(BrainstormTopicAPITestCase):
    def test_member_creates_open_topic_as_author(self):
        response = self.post_topic(category='planning', initial_suggestion='Robótica', author=self.owner.pk)
        self.assertEqual(response.status_code, 201, response.data)
        topic = BrainstormTopic.objects.get(pk=response.data['id'])
        self.assertEqual(topic.author, self.member)
        self.assertEqual((topic.status, topic.cycle, topic.category), ('open', 1, 'planning'))

    def test_validation(self):
        self.assertEqual(self.post_topic(title='a' * 151).status_code, 400)
        self.assertEqual(self.post_topic(context='').status_code, 400)
        self.assertEqual(self.post_topic(category='outra').status_code, 400)

    def test_list_fields_counts_and_my_vote(self):
        BrainstormReply.objects.create(topic=self.topic, author=self.owner, content='Robótica')
        BrainstormVote.objects.create(user=self.owner, topic=self.topic, value=1)
        BrainstormVote.objects.create(user=self.admin, topic=self.topic, value=-1)
        BrainstormVote.objects.create(user=self.member, topic=self.topic, value=1)
        item = client_for(self.admin).get(TOPICS, {'project': self.project.pk}).data['results'][0]
        expected = {
            'replies_count': 1,
            'score': 1,
            'upvotes': 2,
            'downvotes': 1,
            'my_vote': -1,
            'in_review': False,
            'cycle': 1,
            'author_name': self.member.name,
            'status': 'open',
        }
        self.assertEqual({key: item[key] for key in expected}, expected)

    def test_search_filters_and_ordering(self):
        popular = BrainstormTopic.objects.create(
            project=self.project, author=self.owner, title='Uniforme', context='Cores', category='decisions'
        )
        BrainstormVote.objects.create(user=self.owner, topic=popular, value=1)
        client = client_for(self.member)
        self.assertEqual(self.ids(client.get(TOPICS, {'search': 'unifor'})), [popular.pk])
        self.assertEqual(self.ids(client.get(TOPICS, {'category': 'decisions'})), [popular.pk])
        self.assertEqual(
            self.ids(client.get(TOPICS, {'status': 'open', 'ordering': '-score'})), [popular.pk, self.topic.pk]
        )
        self.assertEqual(self.ids(client.get(TOPICS, {'project': self.other_project.pk})), [])


class EditDeleteTests(BrainstormTopicAPITestCase):
    def test_only_author_edits_while_open(self):
        self.assertEqual(client_for(self.owner).patch(topic_url(self.topic), {'title': 'x'}).status_code, 403)
        response = client_for(self.member).patch(topic_url(self.topic), {'title': 'Tema 2026'})
        self.assertEqual(response.status_code, 200, response.data)
        client_for(self.owner).post(topic_url(self.topic, 'close'))
        self.assertEqual(client_for(self.member).patch(topic_url(self.topic), {'title': 'y'}).status_code, 400)

    def test_project_cannot_change(self):
        response = client_for(self.member).patch(topic_url(self.topic), {'project': self.other_project.pk})
        self.assertEqual(response.status_code, 400)

    def test_delete_without_interactions(self):
        self.assertEqual(client_for(self.admin).delete(topic_url(self.topic)).status_code, 204)

    def test_other_member_cannot_delete(self):
        other = make_user('outro')
        Membership.objects.create(project=self.project, user=other, role=Membership.Role.MEMBER)
        self.assertEqual(client_for(other).delete(topic_url(self.topic)).status_code, 403)

    def test_delete_with_interactions_must_archive(self):
        BrainstormReply.objects.create(topic=self.topic, author=self.owner, content='x')
        response = client_for(self.member).delete(topic_url(self.topic))
        self.assertEqual(response.status_code, 400)
        self.assertIn('arquiv', str(response.data).lower())
        self.assertTrue(BrainstormTopic.objects.filter(pk=self.topic.pk).exists())


class TransitionTests(BrainstormTopicAPITestCase):
    def test_member_cannot_manage(self):
        for action in ('close', 'reopen', 'archive'):
            with self.subTest(action=action):
                self.assertEqual(client_for(self.member).post(topic_url(self.topic, action)).status_code, 403)

    def test_close_and_reopen_increment_cycle(self):
        client = client_for(self.admin)
        response = client.post(topic_url(self.topic, 'close'))
        self.assertEqual((response.status_code, response.data['status']), (200, 'closed'))
        self.assertIsNotNone(response.data['closed_at'])
        self.assertEqual(client.post(topic_url(self.topic, 'close')).status_code, 400)
        response = client.post(topic_url(self.topic, 'reopen'))
        self.assertEqual((response.data['status'], response.data['cycle']), ('open', 2))
        self.assertEqual(client.post(topic_url(self.topic, 'reopen')).status_code, 400)

    def test_archive_is_final_and_read_only(self):
        client = client_for(self.owner)
        response = client.post(topic_url(self.topic, 'archive'))
        self.assertEqual((response.status_code, response.data['status']), (200, 'archived'))
        self.assertEqual(client_for(self.member).get(topic_url(self.topic)).status_code, 200)
        self.assertEqual(client_for(self.member).patch(topic_url(self.topic), {'title': 'x'}).status_code, 400)
        for action in ('close', 'reopen', 'archive'):
            with self.subTest(action=action):
                self.assertEqual(client.post(topic_url(self.topic, action)).status_code, 400)
