from django.db import IntegrityError, transaction
from django.test import TestCase

from core.models import Membership, Project

from .helpers import make_project, make_user


class MembershipConstraintTests(TestCase):
    def setUp(self):
        self.ana = make_user('ana')
        self.bia = make_user('bia')
        self.project = make_project(self.ana)

    def test_user_cannot_join_same_project_twice(self):
        Membership.objects.create(project=self.project, user=self.bia)

        with self.assertRaises(IntegrityError), transaction.atomic():
            Membership.objects.create(project=self.project, user=self.bia, role=Membership.Role.ADMIN)

    def test_project_cannot_have_two_owners(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Membership.objects.create(project=self.project, user=self.bia, role=Membership.Role.OWNER)

    def test_roles_are_owner_admin_member(self):
        self.assertEqual(Membership.Role.values, ['owner', 'admin', 'member'])

    def test_deleting_project_removes_memberships(self):
        self.project.delete()

        self.assertFalse(Membership.objects.exists())

    def test_deleting_creator_keeps_project_history_null(self):
        other = Project.objects.create(name='Outro', created_by=self.bia)
        self.bia.delete()
        other.refresh_from_db()

        self.assertIsNone(other.created_by)
