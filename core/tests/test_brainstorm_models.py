from django.db import IntegrityError, transaction
from django.test import TestCase

from core.models import (
    BrainstormAnalysis,
    BrainstormDecision,
    BrainstormReply,
    BrainstormTopic,
    BrainstormVote,
)
from core.tests.helpers import make_project, make_user


class BrainstormModelTests(TestCase):
    def setUp(self):
        self.owner = make_user('dona')
        self.project = make_project(self.owner)
        self.topic = BrainstormTopic.objects.create(
            project=self.project, author=self.owner, title='Tema da feira', context='Escolher o tema.'
        )

    def test_defaults_and_str(self):
        self.assertEqual(self.topic.status, BrainstormTopic.Status.OPEN)
        self.assertEqual(self.topic.category, BrainstormTopic.Category.IDEAS)
        self.assertEqual(self.topic.cycle, 1)
        self.assertEqual(str(self.topic), 'Tema da feira')
        reply = BrainstormReply.objects.create(topic=self.topic, author=self.owner, content='Robótica')
        self.assertIn('Robótica', str(reply))

    def test_in_review_only_when_current_decision_is_from_previous_cycle(self):
        self.assertFalse(self.topic.in_review)
        analysis = BrainstormAnalysis.objects.create(topic=self.topic, cycle=1)
        decision = BrainstormDecision.objects.create(topic=self.topic, cycle=1, analysis=analysis, text='Robótica')
        self.topic.current_decision = decision
        self.assertFalse(self.topic.in_review)
        self.topic.cycle = 2
        self.assertTrue(self.topic.in_review)

    def test_vote_needs_exactly_one_target(self):
        reply = BrainstormReply.objects.create(topic=self.topic, author=self.owner, content='x')
        for target in ({}, {'topic': self.topic, 'reply': reply}):
            with self.subTest(target=target), transaction.atomic(), self.assertRaises(IntegrityError):
                BrainstormVote.objects.create(user=self.owner, value=1, **target)

    def test_one_vote_per_user_and_target(self):
        BrainstormVote.objects.create(user=self.owner, topic=self.topic, value=1)
        with transaction.atomic(), self.assertRaises(IntegrityError):
            BrainstormVote.objects.create(user=self.owner, topic=self.topic, value=-1)
        reply = BrainstormReply.objects.create(topic=self.topic, author=self.owner, content='x')
        BrainstormVote.objects.create(user=self.owner, reply=reply, value=1)
        with transaction.atomic(), self.assertRaises(IntegrityError):
            BrainstormVote.objects.create(user=self.owner, reply=reply, value=1)

    def test_one_decision_per_cycle(self):
        analysis = BrainstormAnalysis.objects.create(topic=self.topic, cycle=1)
        BrainstormDecision.objects.create(topic=self.topic, cycle=1, analysis=analysis, text='a')
        with transaction.atomic(), self.assertRaises(IntegrityError):
            BrainstormDecision.objects.create(topic=self.topic, cycle=1, analysis=analysis, text='b')

    def test_has_interactions(self):
        self.assertFalse(self.topic.has_interactions())
        BrainstormVote.objects.create(user=self.owner, topic=self.topic, value=1)
        self.assertTrue(self.topic.has_interactions())

        other = BrainstormTopic.objects.create(project=self.project, author=self.owner, title='b', context='c')
        BrainstormAnalysis.objects.create(topic=other, cycle=1)
        self.assertTrue(other.has_interactions())

    def test_project_and_topic_deletion_cascade_through_decisions(self):
        analysis = BrainstormAnalysis.objects.create(topic=self.topic, cycle=1, status='ready')
        decision = BrainstormDecision.objects.create(topic=self.topic, cycle=1, analysis=analysis, text='a')
        self.topic.current_decision = decision
        self.topic.save()
        self.project.delete()
        self.assertFalse(BrainstormTopic.objects.exists())
        self.assertFalse(BrainstormDecision.objects.exists())
