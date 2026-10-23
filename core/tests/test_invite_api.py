from datetime import timedelta
from unittest import mock

from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from core.models import Invite, Membership

from .helpers import client_for, make_project, make_user, project_url

INVITES = '/api/invites/'


def invite_url(invite, suffix=''):
    return f'{INVITES}{invite.pk}/{suffix}'


class InviteAPITestCase(APITestCase):
    def setUp(self):
        self.owner = make_user('dono')
        self.admin = make_user('admin')
        self.member = make_user('membro')
        self.guest = make_user('convidado')
        self.other = make_user('outro')
        self.outsider = make_user('externo')
        self.project = make_project(self.owner, admins=[self.admin], members=[self.member])

    def direct_invite(self, user=None, **extra):
        return Invite.objects.create(
            project=self.project,
            kind=Invite.Kind.DIRECT,
            invited_user=user or self.guest,
            invited_by=self.owner,
            **extra,
        )

    def link_invite(self, token='token-de-teste-bem-comprido-123', **extra):
        return Invite.objects.create(
            project=self.project, kind=Invite.Kind.LINK, token=token, invited_by=self.owner, **extra
        )

    def is_member(self, user, role=None):
        memberships = Membership.objects.filter(project=self.project, user=user)
        if role:
            memberships = memberships.filter(role=role)
        return memberships.exists()

    def past(self):
        return timezone.now() - timedelta(days=1)


class AuthenticationTests(InviteAPITestCase):
    def test_endpoints_require_authentication(self):
        anonymous = APIClient()
        invite = self.direct_invite()
        requests = [
            ('get', INVITES),
            ('post', INVITES),
            ('get', f'{INVITES}received/'),
            ('post', f'{INVITES}redeem/'),
            ('get', f'{INVITES}find_user/'),
            ('get', invite_url(invite)),
            ('post', invite_url(invite, 'accept/')),
            ('post', invite_url(invite, 'decline/')),
            ('post', invite_url(invite, 'revoke/')),
        ]
        for method, url in requests:
            with self.subTest(method=method, url=url):
                self.assertEqual(getattr(anonymous, method)(url).status_code, 401)

    def test_invites_cannot_be_edited(self):
        invite = self.direct_invite()

        for method in ('put', 'patch'):
            with self.subTest(method=method):
                response = getattr(client_for(self.owner), method)(invite_url(invite), {'role': 'admin'})
                self.assertEqual(response.status_code, 405)


class CreateDirectInviteTests(InviteAPITestCase):
    def post(self, user, **payload):
        data = {'project': self.project.pk, 'kind': 'direct', 'invited_user': self.guest.pk, **payload}
        return client_for(user).post(INVITES, data)

    def test_owner_invites_registered_user(self):
        response = self.post(self.owner, role='admin')

        self.assertEqual(response.status_code, 201)
        invite = Invite.objects.get(pk=response.data['id'])
        self.assertEqual(
            (invite.invited_user, invite.invited_by, invite.role, invite.status, invite.token),
            (self.guest, self.owner, 'admin', 'pending', None),
        )

    def test_invite_alone_does_not_create_membership(self):
        self.post(self.owner)

        self.assertFalse(self.is_member(self.guest))

    def test_admin_invites_as_member(self):
        response = self.post(self.admin, role='member')

        self.assertEqual(response.status_code, 201)

    def test_admin_cannot_invite_as_admin(self):
        response = self.post(self.admin, role='admin')

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Invite.objects.exists())

    def test_nobody_invites_as_owner(self):
        response = self.post(self.owner, role='owner')

        self.assertEqual(response.status_code, 400)
        self.assertIn('role', response.data)

    def test_member_cannot_invite(self):
        response = self.post(self.member)

        self.assertEqual(response.status_code, 403)

    def test_outsider_gets_404(self):
        response = self.post(self.outsider)

        self.assertEqual(response.status_code, 404)

    def test_user_already_participant_is_rejected(self):
        response = self.post(self.owner, invited_user=self.member.pk)

        self.assertEqual(response.status_code, 400)
        self.assertIn('invited_user', response.data)

    def test_duplicate_pending_invite_is_rejected(self):
        self.direct_invite()

        response = self.post(self.owner)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Invite.objects.count(), 1)

    def test_direct_requires_invited_user(self):
        response = self.post(self.owner, invited_user='')

        self.assertEqual(response.status_code, 400)
        self.assertIn('invited_user', response.data)

    def test_expiration_must_be_in_the_future(self):
        response = self.post(self.owner, expires_at=self.past().isoformat())

        self.assertEqual(response.status_code, 400)
        self.assertIn('expires_at', response.data)

    def test_server_controlled_fields_are_ignored(self):
        response = self.post(self.admin, invited_by=self.owner.pk, status='accepted', token='meu-token')

        invite = Invite.objects.get(pk=response.data['id'])
        self.assertEqual((invite.invited_by, invite.status, invite.token), (self.admin, 'pending', None))


class CreateLinkInviteTests(InviteAPITestCase):
    def post(self, user=None, **payload):
        return client_for(user or self.owner).post(INVITES, {'project': self.project.pk, 'kind': 'link', **payload})

    def test_link_gets_server_generated_token(self):
        response = self.post()

        self.assertEqual(response.status_code, 201)
        token = Invite.objects.get(pk=response.data['id']).token
        self.assertGreaterEqual(len(token), 32)
        self.assertEqual(response.data['token'], token)

    def test_tokens_are_unique(self):
        first = self.post().data['token']
        second = self.post().data['token']

        self.assertNotEqual(first, second)

    def test_link_cannot_target_a_user(self):
        response = self.post(invited_user=self.guest.pk)

        self.assertEqual(response.status_code, 400)
        self.assertIn('invited_user', response.data)

    def test_member_cannot_create_link(self):
        self.assertEqual(self.post(self.member).status_code, 403)


class ListAndDetailTests(InviteAPITestCase):
    def ids(self, response):
        return {item['id'] for item in response.data['results']}

    def test_managers_see_project_invites_with_link_token(self):
        direct, link = self.direct_invite(), self.link_invite()
        for user in (self.owner, self.admin):
            with self.subTest(user=user.email):
                response = client_for(user).get(INVITES, {'project': self.project.pk})
                self.assertEqual(self.ids(response), {direct.pk, link.pk})
                tokens = {item['id']: item['token'] for item in response.data['results']}
                self.assertEqual(tokens[link.pk], link.token)

    def test_member_does_not_see_project_invites(self):
        self.direct_invite()
        self.link_invite()

        self.assertEqual(self.ids(client_for(self.member).get(INVITES)), set())

    def test_invited_user_sees_only_own_invite(self):
        mine = self.direct_invite()
        self.direct_invite(self.other)
        self.link_invite()

        self.assertEqual(self.ids(client_for(self.guest).get(INVITES)), {mine.pk})

    def test_filter_by_status(self):
        pending = self.direct_invite()
        self.direct_invite(self.other, status=Invite.Status.DECLINED)

        response = client_for(self.owner).get(INVITES, {'status': 'pending'})

        self.assertEqual(self.ids(response), {pending.pk})

    def test_invited_user_sees_detail(self):
        response = client_for(self.guest).get(invite_url(self.direct_invite()))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['project_name'], self.project.name)

    def test_member_gets_403_on_detail(self):
        self.assertEqual(client_for(self.member).get(invite_url(self.link_invite())).status_code, 403)

    def test_outsider_gets_404_on_detail(self):
        self.assertEqual(client_for(self.outsider).get(invite_url(self.link_invite())).status_code, 404)


class ReceivedInvitesTests(InviteAPITestCase):
    def test_lists_my_pending_direct_invites(self):
        pending = self.direct_invite()
        other_project = make_project(self.outsider, name='Outro')
        Invite.objects.create(
            project=other_project, kind=Invite.Kind.DIRECT, invited_user=self.guest, status=Invite.Status.DECLINED
        )
        Invite.objects.create(
            project=other_project, kind=Invite.Kind.DIRECT, invited_user=self.guest, expires_at=self.past()
        )
        self.direct_invite(self.other)

        response = client_for(self.guest).get(f'{INVITES}received/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['id'] for item in response.data], [pending.pk])
        self.assertEqual(response.data[0]['project_name'], self.project.name)
        self.assertNotIn('token', response.data[0])


class AcceptDirectInviteTests(InviteAPITestCase):
    def accept(self, invite, user=None):
        return client_for(user or self.guest).post(invite_url(invite, 'accept/'))

    def test_invited_user_accepts_and_joins_with_invite_role(self):
        invite = self.direct_invite(role=Membership.Role.ADMIN)

        response = self.accept(invite)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.is_member(self.guest, Membership.Role.ADMIN))
        invite.refresh_from_db()
        self.assertEqual(invite.status, Invite.Status.ACCEPTED)
        self.assertIsNotNone(invite.responded_at)
        self.assertEqual(client_for(self.guest).get(project_url(self.project)).status_code, 200)

    def test_cannot_accept_twice(self):
        invite = self.direct_invite()
        self.accept(invite)

        response = self.accept(invite)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Membership.objects.filter(project=self.project, user=self.guest).count(), 1)

    def test_only_invited_user_can_accept(self):
        invite = self.direct_invite()

        self.assertEqual(self.accept(invite, self.owner).status_code, 403)
        self.assertEqual(self.accept(invite, self.outsider).status_code, 404)
        self.assertFalse(self.is_member(self.guest))

    def test_expired_invite_cannot_be_accepted(self):
        invite = self.direct_invite(expires_at=self.past())

        response = self.accept(invite)

        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.is_member(self.guest))
        invite.refresh_from_db()
        self.assertEqual(invite.status, Invite.Status.EXPIRED)

    def test_revoked_invite_cannot_be_accepted(self):
        invite = self.direct_invite(status=Invite.Status.REVOKED)

        self.assertEqual(self.accept(invite).status_code, 400)
        self.assertFalse(self.is_member(self.guest))

    def test_already_participant_does_not_duplicate_membership(self):
        invite = self.direct_invite()
        Membership.objects.create(project=self.project, user=self.guest)

        response = self.accept(invite)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Membership.objects.filter(project=self.project, user=self.guest).count(), 1)

    def test_accept_is_atomic(self):
        # A membership é criada antes de o convite ser marcado como aceito; se isso falhar, nada fica.
        invite = self.direct_invite()

        with (
            mock.patch.object(Invite, 'save', side_effect=RuntimeError('falhou')),
            self.assertRaises(RuntimeError),
        ):
            self.accept(invite)

        self.assertFalse(self.is_member(self.guest))
        invite.refresh_from_db()
        self.assertEqual(invite.status, Invite.Status.PENDING)

    def test_link_invite_cannot_be_accepted_by_id(self):
        self.assertIn(self.accept(self.link_invite(), self.owner).status_code, {400, 403})


class DeclineDirectInviteTests(InviteAPITestCase):
    def test_invited_user_declines(self):
        invite = self.direct_invite()

        response = client_for(self.guest).post(invite_url(invite, 'decline/'))

        self.assertEqual(response.status_code, 200)
        invite.refresh_from_db()
        self.assertEqual(invite.status, Invite.Status.DECLINED)
        self.assertFalse(self.is_member(self.guest))

    def test_cannot_decline_after_accepting(self):
        invite = self.direct_invite(status=Invite.Status.ACCEPTED)

        self.assertEqual(client_for(self.guest).post(invite_url(invite, 'decline/')).status_code, 400)

    def test_only_invited_user_can_decline(self):
        self.assertEqual(client_for(self.owner).post(invite_url(self.direct_invite(), 'decline/')).status_code, 403)


class RevokeInviteTests(InviteAPITestCase):
    def revoke(self, invite, user):
        return client_for(user).post(invite_url(invite, 'revoke/'))

    def test_owner_and_admin_revoke_pending_invites(self):
        for user, invite in ((self.owner, self.link_invite()), (self.admin, self.direct_invite())):
            with self.subTest(user=user.email):
                self.assertEqual(self.revoke(invite, user).status_code, 200)
                invite.refresh_from_db()
                self.assertEqual(invite.status, Invite.Status.REVOKED)

    def test_member_cannot_revoke(self):
        self.assertEqual(self.revoke(self.link_invite(), self.member).status_code, 403)

    def test_outsider_gets_404(self):
        self.assertEqual(self.revoke(self.link_invite(), self.outsider).status_code, 404)

    def test_invited_user_cannot_revoke(self):
        self.assertEqual(self.revoke(self.direct_invite(), self.guest).status_code, 403)

    def test_only_pending_invites_can_be_revoked(self):
        invite = self.direct_invite(status=Invite.Status.ACCEPTED)

        self.assertEqual(self.revoke(invite, self.owner).status_code, 400)


class RedeemLinkTests(InviteAPITestCase):
    def redeem(self, user, token):
        return client_for(user).post(f'{INVITES}redeem/', {'token': token})

    def test_redeem_joins_project_and_link_stays_pending(self):
        invite = self.link_invite(role=Membership.Role.ADMIN)

        response = self.redeem(self.guest, invite.token)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['project'], self.project.pk)
        self.assertTrue(self.is_member(self.guest, Membership.Role.ADMIN))
        invite.refresh_from_db()
        self.assertEqual(invite.status, Invite.Status.PENDING)

    def test_link_is_reusable_by_other_users(self):
        invite = self.link_invite()

        self.assertEqual(self.redeem(self.guest, invite.token).status_code, 200)
        self.assertEqual(self.redeem(self.other, invite.token).status_code, 200)
        self.assertTrue(self.is_member(self.other))

    def test_repeated_use_by_same_user_does_not_duplicate_membership(self):
        invite = self.link_invite()
        self.redeem(self.guest, invite.token)

        response = self.redeem(self.guest, invite.token)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Membership.objects.filter(project=self.project, user=self.guest).count(), 1)

    def test_existing_participant_keeps_role(self):
        invite = self.link_invite(role=Membership.Role.ADMIN)

        self.assertEqual(self.redeem(self.member, invite.token).status_code, 400)
        self.assertTrue(self.is_member(self.member, Membership.Role.MEMBER))

    def test_role_and_project_come_from_invite_not_request(self):
        invite = self.link_invite()
        other_project = make_project(self.outsider, name='Outro')

        client_for(self.guest).post(
            f'{INVITES}redeem/', {'token': invite.token, 'role': 'owner', 'project': other_project.pk}
        )

        self.assertTrue(self.is_member(self.guest, Membership.Role.MEMBER))
        self.assertFalse(Membership.objects.filter(project=other_project, user=self.guest).exists())

    def test_unknown_token_is_rejected(self):
        response = self.redeem(self.guest, 'nao-existe')

        self.assertEqual(response.status_code, 400)
        self.assertIn('token', response.data)

    def test_revoked_link_is_rejected(self):
        invite = self.link_invite(status=Invite.Status.REVOKED)

        self.assertEqual(self.redeem(self.guest, invite.token).status_code, 400)
        self.assertFalse(self.is_member(self.guest))

    def test_expired_link_is_rejected_and_marked(self):
        invite = self.link_invite(expires_at=self.past())

        self.assertEqual(self.redeem(self.guest, invite.token).status_code, 400)
        self.assertFalse(self.is_member(self.guest))
        invite.refresh_from_db()
        self.assertEqual(invite.status, Invite.Status.EXPIRED)

    def test_redeem_is_atomic(self):
        invite = self.link_invite()

        with (
            mock.patch.object(Membership.objects, 'create', side_effect=RuntimeError('falhou')),
            self.assertRaises(RuntimeError),
        ):
            self.redeem(self.guest, invite.token)

        self.assertFalse(self.is_member(self.guest))


class FindUserTests(InviteAPITestCase):
    def find(self, user, email, project=None):
        params = {'project': project or self.project.pk, 'email': email}
        return client_for(user).get(f'{INVITES}find_user/', params)

    def test_manager_finds_user_by_exact_email_with_limited_data(self):
        response = self.find(self.admin, self.guest.email.upper())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [{'id': self.guest.pk, 'name': self.guest.name}])

    def test_partial_email_finds_nothing(self):
        self.assertEqual(self.find(self.owner, 'idado@example.com').data, [])

    def test_invalid_email_is_rejected(self):
        response = self.find(self.owner, 'convid')

        self.assertEqual(response.status_code, 400)
        self.assertIn('email', response.data)

    def test_current_participants_are_not_returned(self):
        self.assertEqual(self.find(self.owner, self.member.email).data, [])

    def test_member_cannot_search(self):
        self.assertEqual(self.find(self.member, self.guest.email).status_code, 403)

    def test_outsider_gets_404(self):
        self.assertEqual(self.find(self.outsider, self.guest.email).status_code, 404)

    def test_project_and_email_are_required(self):
        response = client_for(self.owner).get(f'{INVITES}find_user/')

        self.assertEqual(response.status_code, 400)
