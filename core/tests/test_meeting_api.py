from datetime import datetime, timedelta

from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from core.models import Meeting

from .helpers import client_for, make_project, make_user

MEETINGS = '/api/meetings/'
URL = 'https://meet.google.com/abc-defg-hij'


def meeting_url(meeting):
    return f'{MEETINGS}{meeting.pk}/'


class MeetingAPITestCase(APITestCase):
    def setUp(self):
        self.owner = make_user('dono')
        self.admin = make_user('admin')
        self.creator = make_user('criador')
        self.member = make_user('membro')
        self.outsider = make_user('externo')
        self.project = make_project(self.owner, admins=[self.admin], members=[self.creator, self.member])
        self.other_project = make_project(self.outsider, name='Outro projeto')
        self.start = timezone.now() + timedelta(days=2)
        self.meeting = Meeting.objects.create(
            project=self.project, title='Alinhamento', starts_at=self.start, url=URL, created_by=self.creator
        )

    def iso(self, value):
        return value.isoformat()

    def titles(self, params=None, user=None):
        response = client_for(user or self.member).get(MEETINGS, params or {})
        self.assertEqual(response.status_code, 200, response.data)
        return [item['title'] for item in response.data['results']]


class AuthenticationTests(MeetingAPITestCase):
    def test_endpoints_require_authentication(self):
        anonymous = APIClient()
        url = meeting_url(self.meeting)
        for method, path in (('get', MEETINGS), ('post', MEETINGS), ('get', url), ('patch', url), ('delete', url)):
            with self.subTest(method=method, path=path):
                self.assertEqual(getattr(anonymous, method)(path).status_code, 401)


class CreateMeetingTests(MeetingAPITestCase):
    def post(self, user=None, **payload):
        data = {
            'project': self.project.pk,
            'title': 'Ensaio',
            'description': 'Ensaio geral da apresentação',
            'starts_at': self.iso(self.start),
            'url': URL,
            **payload,
        }
        return client_for(user or self.member).post(MEETINGS, data)

    def test_any_member_schedules_meeting(self):
        response = self.post(ends_at=self.iso(self.start + timedelta(hours=1)))

        self.assertEqual(response.status_code, 201)
        meeting = Meeting.objects.get(pk=response.data['id'])
        self.assertEqual((meeting.project, meeting.created_by, meeting.title), (self.project, self.member, 'Ensaio'))
        self.assertEqual(meeting.ends_at, self.start + timedelta(hours=1))

    def test_end_is_optional_and_may_equal_start(self):
        self.assertEqual(self.post().status_code, 201)
        self.assertEqual(self.post(ends_at=self.iso(self.start)).status_code, 201)

    def test_end_before_start_is_rejected(self):
        response = self.post(ends_at=self.iso(self.start - timedelta(minutes=1)))

        self.assertEqual(response.status_code, 400)
        self.assertIn('ends_at', response.data)

    def test_start_is_required(self):
        response = self.post(starts_at='')

        self.assertEqual(response.status_code, 400)
        self.assertIn('starts_at', response.data)

    def test_url_must_be_http_or_https(self):
        for url in ('meet.google.com/abc', 'javascript:alert(1)', 'ftp://exemplo.com/sala', ''):
            with self.subTest(url=url):
                response = self.post(url=url)
                self.assertEqual(response.status_code, 400)
                self.assertIn('url', response.data)

    def test_title_is_required(self):
        response = self.post(title='  ')

        self.assertEqual(response.status_code, 400)
        self.assertIn('title', response.data)

    def test_outsider_gets_404(self):
        self.assertEqual(self.post(self.outsider).status_code, 404)

    def test_created_by_cannot_be_forged(self):
        response = self.post(created_by=self.owner.pk)

        self.assertEqual(Meeting.objects.get(pk=response.data['id']).created_by, self.member)

    def test_dates_are_returned_in_iso_with_timezone(self):
        response = self.post(ends_at=self.iso(self.start + timedelta(hours=1)))

        for field in ('starts_at', 'ends_at', 'created_at'):
            with self.subTest(field=field):
                parsed = datetime.fromisoformat(response.data[field])
                self.assertIsNotNone(parsed.tzinfo)


class ListAndDetailTests(MeetingAPITestCase):
    def setUp(self):
        super().setUp()
        self.past = Meeting.objects.create(
            project=self.project, title='Kickoff', starts_at=timezone.now() - timedelta(days=3), url=URL
        )
        self.later = Meeting.objects.create(
            project=self.project, title='Entrega', starts_at=self.start + timedelta(days=5), url=URL
        )
        Meeting.objects.create(project=self.other_project, title='Alheia', starts_at=self.start, url=URL)

    def test_member_lists_meetings_ordered_by_start(self):
        self.assertEqual(self.titles({'project': self.project.pk}), ['Kickoff', 'Alinhamento', 'Entrega'])

    def test_meetings_of_other_projects_are_isolated(self):
        self.assertNotIn('Alheia', self.titles())
        self.assertEqual(self.titles({'project': self.other_project.pk}), [])

    def test_upcoming_filter(self):
        self.assertEqual(self.titles({'upcoming': 'true'}), ['Alinhamento', 'Entrega'])
        self.assertEqual(self.titles({'upcoming': 'false'}), ['Kickoff'])

    def test_date_range_and_single_day(self):
        params = {
            'starts_at__gte': self.iso(self.start - timedelta(hours=1)),
            'starts_at__lte': self.iso(self.start + timedelta(hours=1)),
        }
        self.assertEqual(self.titles(params), ['Alinhamento'])

        day = timezone.localtime(self.later.starts_at).date().isoformat()
        self.assertEqual(self.titles({'starts_at__date': day}), ['Entrega'])

    def test_member_sees_detail(self):
        response = client_for(self.member).get(meeting_url(self.meeting))

        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.data['title'], response.data['url']), ('Alinhamento', URL))

    def test_outsider_gets_404(self):
        self.assertEqual(client_for(self.outsider).get(meeting_url(self.meeting)).status_code, 404)


class UpdateMeetingTests(MeetingAPITestCase):
    def patch(self, user, **payload):
        return client_for(user).patch(meeting_url(self.meeting), payload)

    def test_creator_owner_and_admin_can_edit(self):
        for user in (self.creator, self.owner, self.admin):
            with self.subTest(user=user.email):
                response = self.patch(user, title=f'Alinhamento {user.pk}')
                self.assertEqual(response.status_code, 200)
                self.meeting.refresh_from_db()
                self.assertEqual(self.meeting.title, f'Alinhamento {user.pk}')

    def test_other_member_cannot_edit(self):
        self.assertEqual(self.patch(self.member, title='Hackeada').status_code, 403)
        self.meeting.refresh_from_db()
        self.assertEqual(self.meeting.title, 'Alinhamento')

    def test_outsider_gets_404(self):
        self.assertEqual(self.patch(self.outsider, title='X').status_code, 404)

    def test_end_before_existing_start_is_rejected(self):
        response = self.patch(self.creator, ends_at=self.iso(self.start - timedelta(hours=1)))

        self.assertEqual(response.status_code, 400)
        self.assertIn('ends_at', response.data)

    def test_start_after_existing_end_is_rejected(self):
        self.meeting.ends_at = self.start + timedelta(hours=1)
        self.meeting.save()

        response = self.patch(self.creator, starts_at=self.iso(self.start + timedelta(hours=2)))

        self.assertEqual(response.status_code, 400)
        self.assertIn('ends_at', response.data)

    def test_project_and_created_by_cannot_change(self):
        own_other = make_project(self.creator, name='Meu outro')

        self.patch(self.creator, created_by=self.member.pk)
        response = self.patch(self.creator, project=own_other.pk)

        self.assertEqual(response.status_code, 400)
        self.meeting.refresh_from_db()
        self.assertEqual((self.meeting.project, self.meeting.created_by), (self.project, self.creator))


class DeleteMeetingTests(MeetingAPITestCase):
    def test_creator_deletes_meeting(self):
        self.assertEqual(client_for(self.creator).delete(meeting_url(self.meeting)).status_code, 204)
        self.assertFalse(Meeting.objects.filter(pk=self.meeting.pk).exists())

    def test_owner_and_admin_delete_others_meetings(self):
        for user in (self.owner, self.admin):
            with self.subTest(user=user.email):
                meeting = Meeting.objects.create(
                    project=self.project, title='Outra', starts_at=self.start, url=URL, created_by=self.creator
                )
                self.assertEqual(client_for(user).delete(meeting_url(meeting)).status_code, 204)

    def test_other_member_cannot_delete(self):
        self.assertEqual(client_for(self.member).delete(meeting_url(self.meeting)).status_code, 403)
        self.assertTrue(Meeting.objects.filter(pk=self.meeting.pk).exists())

    def test_meeting_of_deleted_creator_is_managed_by_owner(self):
        self.creator.delete()

        self.assertEqual(client_for(self.member).delete(meeting_url(self.meeting)).status_code, 403)
        self.assertEqual(client_for(self.owner).delete(meeting_url(self.meeting)).status_code, 204)

    def test_outsider_gets_404(self):
        self.assertEqual(client_for(self.outsider).delete(meeting_url(self.meeting)).status_code, 404)
