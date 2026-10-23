import importlib
from io import StringIO
from unittest import mock

from django.apps import apps as django_apps
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APITestCase

from chat import services
from chat.models import Membership, Message, Room
from core.models import Invite
from core.models import Membership as ProjectMembership
from core.tests.helpers import PROJECTS, client_for, make_project

from .helpers import make_room, make_user


def named(handle, display_name):
    user = make_user(handle)
    type(user).objects.filter(pk=user.pk).update(name=display_name)
    user.refresh_from_db()

    return user


ROOMS = '/api/chat/rooms/'


def chat_room(project):
    return Room.objects.get(project=project)


class ProjectRoomSyncTests(TestCase):
    def setUp(self):
        self.ana = make_user('ana')
        self.bia = make_user('bia')
        self.caio = make_user('caio')

    def test_every_project_has_a_room_with_its_members(self):
        project = make_project(self.ana, 'Feira', admins=[self.bia], members=[self.caio])
        room = chat_room(project)
        self.assertEqual(room.name, 'Feira')
        self.assertEqual(room.created_by, self.ana)
        admins = dict(Membership.objects.filter(room=room).values_list('user__email', 'is_admin'))
        self.assertEqual(admins, {'ana@example.com': True, 'bia@example.com': True, 'caio@example.com': False})

    def test_project_created_through_the_api_gets_a_room(self):
        response = client_for(self.ana).post(PROJECTS, {'name': 'Pela API'}, format='json')
        self.assertEqual(response.status_code, 201)
        room = Room.objects.get(project_id=response.data['id'])
        self.assertEqual(room.name, 'Pela API')
        self.assertEqual(list(Membership.objects.filter(room=room).values_list('user', flat=True)), [self.ana.pk])

    def test_joining_and_leaving_the_project_joins_and_leaves_the_room(self):
        project = make_project(self.ana)
        room = chat_room(project)
        membership = ProjectMembership.objects.create(project=project, user=self.bia)
        self.assertTrue(Membership.objects.filter(room=room, user=self.bia).exists())
        membership.delete()
        self.assertFalse(Membership.objects.filter(room=room, user=self.bia).exists())

    def test_role_changes_follow_the_project(self):
        project = make_project(self.ana, members=[self.bia])
        room = chat_room(project)
        membership = ProjectMembership.objects.get(project=project, user=self.bia)
        membership.role = ProjectMembership.Role.ADMIN
        membership.save()
        self.assertTrue(Membership.objects.get(room=room, user=self.bia).is_admin)
        membership.role = ProjectMembership.Role.MEMBER
        membership.save()
        self.assertFalse(Membership.objects.get(room=room, user=self.bia).is_admin)

    def test_accepting_an_invite_adds_the_user_to_the_room(self):
        project = make_project(self.ana)
        invite = Invite.objects.create(
            project=project, kind=Invite.Kind.DIRECT, invited_user=self.bia, invited_by=self.ana
        )
        self.assertFalse(Membership.objects.filter(room=chat_room(project), user=self.bia).exists())
        response = client_for(self.bia).post(f'/api/invites/{invite.pk}/accept/')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(Membership.objects.filter(room=chat_room(project), user=self.bia).exists())

    def test_renaming_the_project_renames_the_room(self):
        project = make_project(self.ana, 'Antigo')
        project.name = 'Novo nome'
        project.save()
        self.assertEqual(chat_room(project).name, 'Novo nome')

    def test_removed_member_keeps_history_and_regains_access_when_back(self):
        project = make_project(self.ana, members=[self.bia])
        room = chat_room(project)
        services.send_message(self.bia, room, body='oi')
        ProjectMembership.objects.get(project=project, user=self.bia).delete()
        self.assertIsNone(services.get_room_for(self.bia, room.pk))
        self.assertEqual(Message.objects.filter(room=room).count(), 1)
        ProjectMembership.objects.create(project=project, user=self.bia)
        self.assertIsNotNone(services.get_room_for(self.bia, room.pk))

    def test_deleting_the_project_deletes_room_and_messages(self):
        project = make_project(self.ana, members=[self.bia])
        room = chat_room(project)
        services.send_message(self.ana, room, body='oi')
        project.delete()
        self.assertFalse(Room.objects.filter(pk=room.pk).exists())
        self.assertEqual(Message.objects.count(), 0)

    def test_inactive_users_do_not_join_the_room(self):
        inactive = make_user('inativo', is_active=False)
        project = make_project(self.ana, members=[inactive])
        self.assertFalse(Membership.objects.filter(room=chat_room(project), user=inactive).exists())

    def test_changes_are_broadcast_after_commit(self):
        project = make_project(self.ana)
        with mock.patch('chat.project_rooms.realtime.broadcast_room_changed') as broadcast:
            with self.captureOnCommitCallbacks(execute=True):
                ProjectMembership.objects.create(project=project, user=self.bia)
        broadcast.assert_called_once()
        room_id, user_ids, removed = broadcast.call_args.args
        self.assertEqual(room_id, chat_room(project).pk)
        self.assertEqual(set(user_ids), {self.ana.pk, self.bia.pk})
        self.assertEqual(removed, [])
        with mock.patch('chat.project_rooms.realtime.broadcast_room_changed') as broadcast:
            with self.captureOnCommitCallbacks(execute=True):
                ProjectMembership.objects.get(project=project, user=self.bia).delete()
        room_id, user_ids, removed = broadcast.call_args.args
        self.assertEqual(removed, [self.bia.pk])
        self.assertIn(self.bia.pk, user_ids)

    def test_nothing_is_broadcast_when_nothing_changes(self):
        project = make_project(self.ana)
        with mock.patch('chat.project_rooms.realtime.broadcast_room_changed') as broadcast:
            with self.captureOnCommitCallbacks(execute=True):
                project.description = 'só a descrição'
                project.save()
        broadcast.assert_not_called()

    def test_migration_creates_rooms_for_existing_projects(self):
        project = make_project(self.ana, admins=[self.bia], members=[self.caio])
        Room.objects.filter(project=project).delete()
        migration = importlib.import_module('chat.migrations.0003_rooms_for_existing_projects')
        migration.create_rooms(django_apps, None)
        migration.create_rooms(django_apps, None)  # idempotente
        self.assertEqual(Room.objects.filter(project=project).count(), 1)
        admins = dict(Membership.objects.filter(room__project=project).values_list('user__email', 'is_admin'))
        self.assertEqual(admins, {'ana@example.com': True, 'bia@example.com': True, 'caio@example.com': False})


class ProjectRoomAPITests(APITestCase):
    def setUp(self):
        self.ana = named('ana', 'Ana Souza')
        self.bia = named('bia', 'Beatriz Lima')
        self.caio = make_user('caio')
        self.project = make_project(self.ana, 'Feira', members=[self.bia])
        self.other = make_project(self.caio, 'Outro')

    def test_room_list_exposes_the_project_and_can_be_filtered(self):
        client = client_for(self.ana)
        everything = client.get(ROOMS).data['results']
        self.assertEqual([room['project'] for room in everything], [self.project.pk])
        only = client.get(ROOMS, {'project': self.project.pk}).data['results']
        self.assertEqual(len(only), 1)
        self.assertEqual(client.get(ROOMS, {'project': self.other.pk}).data['count'], 0)
        self.assertEqual(client.get(ROOMS, {'project': 'x'}).status_code, 400)

    def test_detail_shows_project_roles(self):
        room = chat_room(self.project)
        data = client_for(self.bia).get(f'{ROOMS}{room.pk}/').data
        roles = {member['user']['id']: member['role'] for member in data['members']}
        self.assertEqual(roles, {self.ana.pk: 'owner', self.bia.pk: 'member'})
        self.assertEqual(data['project'], self.project.pk)

    def test_non_participant_cannot_see_the_project_room(self):
        room = chat_room(self.project)
        self.assertEqual(client_for(self.caio).get(f'{ROOMS}{room.pk}/').status_code, 404)

    def test_user_search_matches_name_and_shows_the_profile_name(self):
        results = client_for(self.caio).get('/api/chat/users/', {'search': 'beat'}).data['results']
        self.assertEqual([user['name'] for user in results], ['Beatriz Lima'])
        by_email = client_for(self.caio).get('/api/chat/users/', {'search': 'bia@'}).data['results']
        self.assertEqual([user['id'] for user in by_email], [self.bia.pk])
        # sem nome no perfil, cai para a parte local do e-mail
        fallback = client_for(self.ana).get('/api/chat/users/', {'search': 'caio'}).data['results'][0]
        self.assertEqual(fallback['name'], 'caio')

    def test_exclude_project_hides_members_and_pending_invites(self):
        dani = named('dani', 'Daniela')
        eva = named('eva', 'Evandro')
        Invite.objects.create(project=self.project, kind=Invite.Kind.DIRECT, invited_user=dani, invited_by=self.ana)
        client = client_for(self.ana)
        everyone = {u['id'] for u in client.get('/api/chat/users/', {'search': 'example'}).data['results']}
        self.assertTrue({self.bia.pk, dani.pk, eva.pk} <= everyone)
        params = {'search': 'example', 'exclude_project': self.project.pk}
        filtered = {u['id'] for u in client.get('/api/chat/users/', params).data['results']}
        self.assertNotIn(self.bia.pk, filtered)
        self.assertNotIn(dani.pk, filtered)
        self.assertIn(eva.pk, filtered)
        self.assertEqual(
            client.get('/api/chat/users/', {'search': 'example', 'exclude_project': 'x'}).status_code, 400
        )

    def test_exclude_project_is_ignored_for_non_participants(self):
        # Quem não participa do projeto não descobre quem participa dele.
        results = client_for(self.caio).get(
            '/api/chat/users/', {'search': 'example', 'exclude_project': self.project.pk}
        ).data['results']
        self.assertIn(self.bia.pk, {user['id'] for user in results})


class DeleteStandaloneRoomsTests(TestCase):
    def setUp(self):
        self.ana = make_user('ana')
        self.bia = make_user('bia')
        self.project = make_project(self.ana)
        self.old = make_room(self.ana, self.bia, name='Avulsa')
        services.send_message(self.ana, self.old, body='antiga')

    def run_command(self, *args):
        out = StringIO()
        call_command('delete_standalone_rooms', *args, stdout=out)
        return out.getvalue()

    def test_dry_run_changes_nothing(self):
        output = self.run_command()
        self.assertIn('Avulsa', output)
        self.assertIn('--yes', output)
        self.assertTrue(Room.objects.filter(pk=self.old.pk).exists())

    def test_yes_deletes_only_standalone_rooms_and_keeps_users(self):
        self.run_command('--yes')
        self.assertFalse(Room.objects.filter(pk=self.old.pk).exists())
        self.assertEqual(Message.objects.count(), 0)
        self.assertTrue(Room.objects.filter(project=self.project).exists())
        self.assertEqual(type(self.ana).objects.filter(pk__in=[self.ana.pk, self.bia.pk]).count(), 2)


class DisplayNameTests(TestCase):
    def test_nome_padrao_da_migration_cai_para_o_email(self):
        from chat.serializers.user import display_name

        user = named('maria.silva', 'Usuário')
        self.assertEqual(display_name(user), user.email.split('@')[0])

    def test_nome_escolhido_e_mantido(self):
        from chat.serializers.user import display_name

        self.assertEqual(display_name(named('joao', 'João Pereira')), 'João Pereira')
