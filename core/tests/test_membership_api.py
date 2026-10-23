from unittest import mock

from rest_framework.test import APIClient, APITestCase

from core.models import Membership

from .helpers import client_for, make_project, make_user, project_url

MEMBERSHIPS = '/api/memberships/'


def membership_url(membership):
    return f'{MEMBERSHIPS}{membership.pk}/'


class MembershipAPITestCase(APITestCase):
    def setUp(self):
        self.owner = make_user('dono')
        self.admin = make_user('admin')
        self.admin2 = make_user('admin2')
        self.member = make_user('membro')
        self.member2 = make_user('membro2')
        self.outsider = make_user('externo')
        self.project = make_project(self.owner, admins=[self.admin, self.admin2], members=[self.member, self.member2])
        self.other_project = make_project(self.outsider, name='Outro projeto', members=[self.owner])

    def membership_of(self, user, project=None):
        return Membership.objects.get(project=project or self.project, user=user)

    def owners_of(self, project):
        owners = Membership.objects.filter(project=project, role=Membership.Role.OWNER)
        return list(owners.values_list('user', flat=True))


class AuthenticationTests(MembershipAPITestCase):
    def test_endpoints_require_authentication(self):
        anonymous = APIClient()
        url = membership_url(self.membership_of(self.member))
        for method, path in (('get', MEMBERSHIPS), ('get', url), ('patch', url), ('delete', url)):
            with self.subTest(method=method, path=path):
                self.assertEqual(getattr(anonymous, method)(path).status_code, 401)

    def test_memberships_cannot_be_created_directly(self):
        response = client_for(self.owner).post(
            MEMBERSHIPS, {'project': self.project.pk, 'user': self.outsider.pk, 'role': 'member'}
        )

        self.assertEqual(response.status_code, 405)


class ListAndDetailTests(MembershipAPITestCase):
    def test_member_lists_participants_and_roles_of_project(self):
        response = client_for(self.member).get(MEMBERSHIPS, {'project': self.project.pk})

        self.assertEqual(response.status_code, 200)
        roles = {item['user']: item['role'] for item in response.data['results']}
        self.assertEqual(
            roles,
            {
                self.owner.pk: 'owner',
                self.admin.pk: 'admin',
                self.admin2.pk: 'admin',
                self.member.pk: 'member',
                self.member2.pk: 'member',
            },
        )

    def test_list_shows_participant_email(self):
        response = client_for(self.member).get(MEMBERSHIPS, {'project': self.project.pk})

        emails = {item['user_email'] for item in response.data['results']}
        self.assertIn(self.owner.email, emails)

    def test_list_only_includes_projects_user_participates_in(self):
        response = client_for(self.member).get(MEMBERSHIPS)

        self.assertEqual({item['project'] for item in response.data['results']}, {self.project.pk})

    def test_filter_by_project_of_outsider_returns_nothing(self):
        response = client_for(self.member).get(MEMBERSHIPS, {'project': self.other_project.pk})

        self.assertEqual(response.data['results'], [])

    def test_member_sees_detail(self):
        response = client_for(self.member).get(membership_url(self.membership_of(self.admin)))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['role'], 'admin')
        self.assertEqual(response.data['user_email'], self.admin.email)

    def test_outsider_gets_404(self):
        response = client_for(self.outsider).get(membership_url(self.membership_of(self.member)))

        self.assertEqual(response.status_code, 404)


class ChangeRoleTests(MembershipAPITestCase):
    def test_owner_promotes_member_to_admin(self):
        membership = self.membership_of(self.member)

        response = client_for(self.owner).patch(membership_url(membership), {'role': 'admin'})

        self.assertEqual(response.status_code, 200)
        membership.refresh_from_db()
        self.assertEqual(membership.role, Membership.Role.ADMIN)

    def test_owner_demotes_admin_to_member(self):
        membership = self.membership_of(self.admin)

        response = client_for(self.owner).patch(membership_url(membership), {'role': 'member'})

        self.assertEqual(response.status_code, 200)
        membership.refresh_from_db()
        self.assertEqual(membership.role, Membership.Role.MEMBER)

    def test_admin_and_member_cannot_change_roles(self):
        membership = self.membership_of(self.member2)
        for user in (self.admin, self.member):
            with self.subTest(user=user.email):
                response = client_for(user).patch(membership_url(membership), {'role': 'admin'})
                self.assertEqual(response.status_code, 403)
                membership.refresh_from_db()
                self.assertEqual(membership.role, Membership.Role.MEMBER)

    def test_outsider_gets_404(self):
        response = client_for(self.outsider).patch(membership_url(self.membership_of(self.member)), {'role': 'admin'})

        self.assertEqual(response.status_code, 404)

    def test_owner_of_another_project_cannot_change_roles_here(self):
        # outsider é dono de other_project, onde self.owner é só membro.
        membership = Membership.objects.get(project=self.other_project, user=self.owner)

        response = client_for(self.owner).patch(membership_url(membership), {'role': 'admin'})

        self.assertEqual(response.status_code, 403)

    def test_invalid_role_is_rejected(self):
        response = client_for(self.owner).patch(membership_url(self.membership_of(self.member)), {'role': 'chefe'})

        self.assertEqual(response.status_code, 400)
        self.assertIn('role', response.data)

    def test_owner_cannot_demote_self(self):
        response = client_for(self.owner).patch(membership_url(self.membership_of(self.owner)), {'role': 'admin'})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.owners_of(self.project), [self.owner.pk])

    def test_project_and_user_cannot_be_changed(self):
        membership = self.membership_of(self.member)

        client_for(self.owner).patch(
            membership_url(membership), {'project': self.other_project.pk, 'user': self.outsider.pk, 'role': 'admin'}
        )
        membership.refresh_from_db()

        self.assertEqual((membership.project, membership.user), (self.project, self.member))


class TransferOwnershipTests(MembershipAPITestCase):
    def test_owner_transfers_ownership(self):
        response = client_for(self.owner).patch(membership_url(self.membership_of(self.member)), {'role': 'owner'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.owners_of(self.project), [self.member.pk])
        self.assertEqual(self.membership_of(self.owner).role, Membership.Role.ADMIN)

    def test_new_owner_can_delete_project_and_old_owner_cannot(self):
        client_for(self.owner).patch(membership_url(self.membership_of(self.admin)), {'role': 'owner'})

        self.assertEqual(client_for(self.owner).delete(project_url(self.project)).status_code, 403)
        self.assertEqual(client_for(self.admin).delete(project_url(self.project)).status_code, 204)

    def test_admin_cannot_take_ownership(self):
        response = client_for(self.admin).patch(membership_url(self.membership_of(self.admin)), {'role': 'owner'})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.owners_of(self.project), [self.owner.pk])

    def test_transfer_is_atomic(self):
        # 1º save rebaixa o dono atual; o 2º (promover o novo dono) falha.
        failing_save = 2
        original_save = Membership.save
        calls = []

        def save_then_fail(instance, *args, **kwargs):
            calls.append(instance.pk)
            if len(calls) == failing_save:
                raise RuntimeError('falhou no meio da transferência')
            return original_save(instance, *args, **kwargs)

        with mock.patch.object(Membership, 'save', save_then_fail), self.assertRaises(RuntimeError):
            client_for(self.owner).patch(membership_url(self.membership_of(self.member)), {'role': 'owner'})

        self.assertEqual(self.owners_of(self.project), [self.owner.pk])
        self.assertEqual(self.membership_of(self.member).role, Membership.Role.MEMBER)


class RemoveParticipantTests(MembershipAPITestCase):
    def assert_removed(self, user, by):
        response = client_for(by).delete(membership_url(self.membership_of(user)))

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Membership.objects.filter(project=self.project, user=user).exists())

    def test_owner_removes_member_and_admin(self):
        self.assert_removed(self.member, by=self.owner)
        self.assert_removed(self.admin, by=self.owner)

    def test_admin_removes_member(self):
        self.assert_removed(self.member, by=self.admin)

    def test_admin_cannot_remove_admin_or_owner(self):
        for target in (self.admin2, self.owner):
            with self.subTest(target=target.email):
                response = client_for(self.admin).delete(membership_url(self.membership_of(target)))
                self.assertEqual(response.status_code, 403)
                self.assertTrue(Membership.objects.filter(project=self.project, user=target).exists())

    def test_member_cannot_remove_anyone(self):
        response = client_for(self.member).delete(membership_url(self.membership_of(self.member2)))

        self.assertEqual(response.status_code, 403)

    def test_owner_cannot_be_removed(self):
        response = client_for(self.owner).delete(membership_url(self.membership_of(self.owner)))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.owners_of(self.project), [self.owner.pk])

    def test_outsider_gets_404(self):
        response = client_for(self.outsider).delete(membership_url(self.membership_of(self.member)))

        self.assertEqual(response.status_code, 404)

    def test_removed_member_loses_access_to_project(self):
        self.assert_removed(self.member, by=self.owner)

        self.assertEqual(client_for(self.member).get(project_url(self.project)).status_code, 404)
