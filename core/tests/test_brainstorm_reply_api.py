from rest_framework.test import APIClient, APITestCase

from core.models import BrainstormReply, BrainstormTopic, BrainstormVote

from .helpers import client_for, make_project, make_user
from .test_brainstorm_topic_api import topic_url

REPLIES = '/api/brainstorm-replies/'


def reply_url(reply, action=''):
    return f'{REPLIES}{reply.pk}/{action + "/" if action else ""}'


class BrainstormReplyAPITestCase(APITestCase):
    def setUp(self):
        self.owner = make_user('dona')
        self.member = make_user('membro')
        self.other = make_user('colega')
        self.outsider = make_user('externo')
        self.project = make_project(self.owner, members=[self.member, self.other])
        self.other_project = make_project(self.outsider, name='Outro projeto')
        self.topic = BrainstormTopic.objects.create(
            project=self.project, author=self.owner, title='Tema', context='Escolher o tema.'
        )
        self.other_topic = BrainstormTopic.objects.create(
            project=self.project, author=self.owner, title='Outro', context='x'
        )
        self.reply = BrainstormReply.objects.create(topic=self.topic, author=self.member, content='Robótica')

    def post_reply(self, user=None, **payload):
        data = {'topic': self.topic.pk, 'content': 'Sustentabilidade', **payload}
        return client_for(user or self.other).post(REPLIES, data)

    def close(self):
        self.topic.status = BrainstormTopic.Status.CLOSED
        self.topic.save()


class ReplyTests(BrainstormReplyAPITestCase):
    def test_requires_authentication(self):
        self.assertEqual(APIClient().get(REPLIES).status_code, 401)

    def test_member_replies_and_answers_reply(self):
        response = self.post_reply(parent=self.reply.pk, author=self.owner.pk)
        self.assertEqual(response.status_code, 201, response.data)
        child = BrainstormReply.objects.get(pk=response.data['id'])
        self.assertEqual((child.author, child.parent), (self.other, self.reply))

    def test_list_by_topic_exposes_parent_and_votes(self):
        child = BrainstormReply.objects.create(topic=self.topic, author=self.other, content='+1', parent=self.reply)
        BrainstormVote.objects.create(user=self.owner, reply=self.reply, value=1)
        results = client_for(self.owner).get(REPLIES, {'topic': self.topic.pk}).data['results']
        self.assertEqual(
            [(item['id'], item['parent']) for item in results], [(self.reply.pk, None), (child.pk, self.reply.pk)]
        )
        self.assertEqual(
            (results[0]['score'], results[0]['my_vote'], results[0]['author_name']), (1, 1, self.member.name)
        )

    def test_parent_from_other_topic_is_rejected(self):
        foreign = BrainstormReply.objects.create(topic=self.other_topic, author=self.member, content='x')
        self.assertEqual(self.post_reply(parent=foreign.pk).status_code, 400)

    def test_validation_and_closed_topic(self):
        self.assertEqual(self.post_reply(content='').status_code, 400)
        self.assertEqual(self.post_reply(content='a' * 5001).status_code, 400)
        self.close()
        self.assertEqual(self.post_reply().status_code, 400)

    def test_outsider(self):
        client = client_for(self.outsider)
        self.assertEqual(client.get(REPLIES, {'topic': self.topic.pk}).data['results'], [])
        self.assertEqual(client.get(reply_url(self.reply)).status_code, 404)
        self.assertEqual(self.post_reply(self.outsider).status_code, 404)

    def test_only_author_edits_while_open(self):
        self.assertEqual(client_for(self.owner).patch(reply_url(self.reply), {'content': 'x'}).status_code, 403)
        response = client_for(self.member).patch(reply_url(self.reply), {'content': 'Robótica educacional'})
        self.assertEqual(response.status_code, 200, response.data)
        moved = client_for(self.member).patch(reply_url(self.reply), {'topic': self.other_topic.pk})
        self.assertEqual(moved.status_code, 400)
        self.close()
        self.assertEqual(client_for(self.member).patch(reply_url(self.reply), {'content': 'y'}).status_code, 400)

    def test_delete_rules(self):
        self.assertEqual(client_for(self.other).delete(reply_url(self.reply)).status_code, 403)
        child = BrainstormReply.objects.create(topic=self.topic, author=self.other, content='+1', parent=self.reply)
        self.assertEqual(client_for(self.member).delete(reply_url(self.reply)).status_code, 400)
        self.assertEqual(client_for(self.owner).delete(reply_url(child)).status_code, 204)
        self.assertEqual(client_for(self.member).delete(reply_url(self.reply)).status_code, 204)


class VoteTests(BrainstormReplyAPITestCase):
    def vote(self, url, value, user=None):
        return client_for(user or self.member).post(url, {'value': value}, format='json')

    def test_topic_vote_change_and_remove(self):
        url = topic_url(self.topic, 'vote')
        response = self.vote(url, 1)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data, {'score': 1, 'upvotes': 1, 'downvotes': 0, 'my_vote': 1})
        self.vote(url, 1)
        self.assertEqual(BrainstormVote.objects.filter(topic=self.topic).count(), 1)
        self.vote(url, -1, self.other)
        response = self.vote(url, -1)
        self.assertEqual(response.data, {'score': -2, 'upvotes': 0, 'downvotes': 2, 'my_vote': -1})
        response = self.vote(url, 0)
        self.assertEqual(response.data, {'score': -1, 'upvotes': 0, 'downvotes': 1, 'my_vote': 0})
        self.assertEqual(self.vote(url, 0).status_code, 200)

    def test_reply_vote(self):
        response = self.vote(reply_url(self.reply, 'vote'), -1, self.owner)
        self.assertEqual(response.data, {'score': -1, 'upvotes': 0, 'downvotes': 1, 'my_vote': -1})
        self.assertTrue(BrainstormVote.objects.filter(reply=self.reply, topic=None).exists())

    def test_invalid_value_and_blocked_status(self):
        for value in (2, 'x', None):
            with self.subTest(value=value):
                self.assertEqual(self.vote(topic_url(self.topic, 'vote'), value).status_code, 400)
        self.close()
        self.assertEqual(self.vote(topic_url(self.topic, 'vote'), 1).status_code, 400)
        self.assertEqual(self.vote(reply_url(self.reply, 'vote'), 1).status_code, 400)

    def test_outsider_cannot_vote(self):
        self.assertEqual(self.vote(topic_url(self.topic, 'vote'), 1, self.outsider).status_code, 404)
        self.assertEqual(self.vote(reply_url(self.reply, 'vote'), 1, self.outsider).status_code, 404)
