from datetime import timedelta

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from core.models import Meeting

from .helpers import make_project, make_user


class MeetingModelTests(TestCase):
    def setUp(self):
        self.owner = make_user('dono')
        self.project = make_project(self.owner)
        self.start = timezone.now() + timedelta(days=1)

    def make(self, **extra):
        data = {
            'project': self.project,
            'title': 'Alinhamento',
            'starts_at': self.start,
            'url': 'https://meet.google.com/abc',
        }
        return Meeting.objects.create(**{**data, **extra})

    def test_str(self):
        self.assertEqual(str(self.make()), 'Alinhamento')

    def test_meetings_are_ordered_by_start(self):
        later = self.make(title='Depois', starts_at=self.start + timedelta(hours=2))
        sooner = self.make(title='Antes', starts_at=self.start - timedelta(hours=2))

        self.assertEqual(list(self.project.meetings.all()), [sooner, later])

    def test_database_rejects_end_before_start(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make(ends_at=self.start - timedelta(minutes=1))

    def test_end_is_optional(self):
        self.assertIsNone(self.make().ends_at)

    def test_deleting_creator_keeps_meeting(self):
        meeting = self.make(created_by=self.owner)

        self.owner.delete()
        meeting.refresh_from_db()

        self.assertIsNone(meeting.created_by)

    def test_deleting_project_deletes_meetings(self):
        self.make()

        self.project.delete()

        self.assertFalse(Meeting.objects.exists())
