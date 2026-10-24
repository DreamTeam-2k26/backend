from django.db import IntegrityError, transaction
from django.test import TestCase

from core.models import Invite

from .helpers import make_project, make_user


class InviteConstraintTests(TestCase):
    def setUp(self):
        self.owner = make_user('dono')
        self.guest = make_user('convidado')
        self.project = make_project(self.owner)

    def assert_integrity_error(self, **fields):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Invite.objects.create(project=self.project, invited_by=self.owner, **fields)

    def test_link_requires_token(self):
        self.assert_integrity_error(kind=Invite.Kind.LINK)

    def test_direct_requires_invited_user(self):
        self.assert_integrity_error(kind=Invite.Kind.DIRECT)

    def test_direct_cannot_have_token(self):
        self.assert_integrity_error(kind=Invite.Kind.DIRECT, invited_user=self.guest, token='abc')

    def test_token_is_unique(self):
        Invite.objects.create(project=self.project, kind=Invite.Kind.LINK, token='mesmo-token')

        self.assert_integrity_error(kind=Invite.Kind.LINK, token='mesmo-token')

    def test_one_pending_direct_invite_per_user_and_project(self):
        Invite.objects.create(project=self.project, kind=Invite.Kind.DIRECT, invited_user=self.guest)

        self.assert_integrity_error(kind=Invite.Kind.DIRECT, invited_user=self.guest)

    def test_new_direct_invite_allowed_after_previous_was_declined(self):
        Invite.objects.create(
            project=self.project, kind=Invite.Kind.DIRECT, invited_user=self.guest, status=Invite.Status.DECLINED
        )

        Invite.objects.create(project=self.project, kind=Invite.Kind.DIRECT, invited_user=self.guest)

    def test_status_and_kind_choices(self):
        self.assertEqual(Invite.Kind.values, ['direct', 'link'])
        self.assertEqual(Invite.Status.values, ['pending', 'accepted', 'declined', 'revoked', 'expired'])
