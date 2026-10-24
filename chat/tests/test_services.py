from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from chat import services
from chat.models import Membership, Message, Room

from .helpers import HTML_BYTES, JPEG_BYTES, TempMediaMixin, make_room, make_user


class RoomServiceTests(TestCase):
    def setUp(self):
        self.ana = make_user('ana')
        self.bia = make_user('bia')
        self.caio = make_user('caio')

    def test_create_room_makes_creator_admin_and_others_members(self):
        room = services.create_room(self.ana, '  Projeto  ', [self.bia, self.ana])
        memberships = {m.user_id: m.is_admin for m in Membership.objects.filter(room=room)}
        self.assertEqual(room.name, 'Projeto')
        self.assertEqual(memberships, {self.ana.pk: True, self.bia.pk: False})
        self.assertEqual(room.created_by, self.ana)

    def test_create_room_skips_inactive_users(self):
        inactive = make_user('inativo', is_active=False)
        room = services.create_room(self.ana, 'X', [inactive, self.bia])
        self.assertEqual(set(services.member_ids(room)), {self.ana.pk, self.bia.pk})

    def test_room_name_is_required_and_limited(self):
        for bad in ('', '   ', None, 'x' * 101, 123):
            with self.subTest(name=bad), self.assertRaises(ValidationError):
                services.create_room(self.ana, bad)
        self.assertEqual(Room.objects.count(), 0)

    def test_get_membership_denies_non_members_and_anonymous(self):
        room = make_room(self.ana, self.bia)
        self.assertTrue(services.get_membership(self.bia, room))
        with self.assertRaises(PermissionDenied):
            services.get_membership(self.caio, room)

    def test_get_room_for_hides_rooms_from_non_members(self):
        room = make_room(self.ana)
        self.assertEqual(services.get_room_for(self.ana, room.pk), room)
        self.assertIsNone(services.get_room_for(self.caio, room.pk))
        self.assertIsNone(services.get_room_for(self.caio, 999999))

    def test_only_admin_can_manage(self):
        room = make_room(self.ana, self.bia)
        with self.assertRaises(PermissionDenied):
            services.add_members(self.bia, room, [self.caio])
        with self.assertRaises(PermissionDenied):
            services.remove_member(self.bia, room, self.ana)
        with self.assertRaises(PermissionDenied):
            services.set_admin(self.bia, room, self.bia, True)
        with self.assertRaises(PermissionDenied):
            services.update_room(self.bia, room, name='Novo')
        self.assertEqual(Membership.objects.filter(room=room).count(), 2)

    def test_add_members_is_idempotent(self):
        room = make_room(self.ana, self.bia)
        added = services.add_members(self.ana, room, [self.bia, self.caio])
        self.assertEqual(added, [self.caio])
        self.assertEqual(services.add_members(self.ana, room, [self.caio]), [])
        self.assertEqual(Membership.objects.filter(room=room).count(), 3)

    def test_remove_member(self):
        room = make_room(self.ana, self.bia)
        services.remove_member(self.ana, room, self.bia)
        self.assertIsNone(services.get_room_for(self.bia, room.pk))
        with self.assertRaises(ValidationError):
            services.remove_member(self.ana, room, self.bia)  # já saiu
        with self.assertRaises(ValidationError):
            services.remove_member(self.ana, room, self.ana)  # para sair, leave_room

    def test_set_admin_keeps_at_least_one_admin(self):
        room = make_room(self.ana, self.bia)
        with self.assertRaises(ValidationError):
            services.set_admin(self.ana, room, self.ana, False)
        services.set_admin(self.ana, room, self.bia, True)
        services.set_admin(self.ana, room, self.ana, False)
        self.assertTrue(Membership.objects.get(room=room, user=self.bia).is_admin)
        with self.assertRaises(ValidationError):
            services.set_admin(self.bia, room, self.caio, True)  # caio não participa

    def test_update_room(self):
        room = make_room(self.ana)
        services.update_room(self.ana, room, name='  Novo nome ')
        room.refresh_from_db()
        self.assertEqual(room.name, 'Novo nome')
        with self.assertRaises(ValidationError):
            services.update_room(self.ana, room, name='')

    def test_leave_promotes_oldest_member_when_last_admin_leaves(self):
        room = make_room(self.ana, self.bia, self.caio)
        services.leave_room(self.ana, room)
        self.assertTrue(Membership.objects.get(room=room, user=self.bia).is_admin)
        self.assertFalse(Membership.objects.get(room=room, user=self.caio).is_admin)

    def test_leave_deletes_empty_room(self):
        room = make_room(self.ana)
        room_id = room.pk
        services.leave_room(self.ana, room)
        self.assertFalse(Room.objects.filter(pk=room_id).exists())

    def test_leave_requires_membership(self):
        room = make_room(self.ana)
        with self.assertRaises(PermissionDenied):
            services.leave_room(self.caio, room)
        self.assertTrue(Room.objects.filter(pk=room.pk).exists())


class MessageServiceTests(TempMediaMixin, TestCase):
    def setUp(self):
        self.ana = make_user('ana')
        self.bia = make_user('bia')
        self.caio = make_user('caio')
        self.room = make_room(self.ana, self.bia)

    def test_send_text_persists_and_updates_room(self):
        message = services.send_message(self.ana, self.room, body='  oi  ')
        self.assertEqual(message.body, 'oi')
        self.assertEqual(message.author, self.ana)
        self.room.refresh_from_db()
        self.assertEqual(self.room.last_message_at, message.created)

    def test_non_member_cannot_send(self):
        with self.assertRaises(PermissionDenied):
            services.send_message(self.caio, self.room, body='intruso')
        self.assertEqual(Message.objects.count(), 0)

    def test_body_validation(self):
        for bad in ('', '   ', 'x' * 2001, 42, ['a']):
            with self.subTest(body=bad), self.assertRaises(ValidationError):
                services.send_message(self.ana, self.room, body=bad)
        self.assertEqual(Message.objects.count(), 0)

    def test_requires_exactly_one_of_body_or_file(self):
        upload = SimpleUploadedFile('a.jpg', JPEG_BYTES)
        with self.assertRaises(ValidationError):
            services.send_message(self.ana, self.room)
        with self.assertRaises(ValidationError):
            services.send_message(self.ana, self.room, body='x', file=upload)

    def test_reply_must_belong_to_same_room(self):
        other = make_room(self.ana, name='Outra')
        foreign = services.send_message(self.ana, other, body='fora')
        with self.assertRaises(ValidationError):
            services.send_message(self.ana, self.room, body='r', reply_to_id=foreign.pk)
        original = services.send_message(self.bia, self.room, body='base')
        reply = services.send_message(self.ana, self.room, body='r', reply_to_id=original.pk)
        self.assertEqual(reply.reply_to, original)

    def test_file_records_real_content_type_not_the_extension(self):
        upload = SimpleUploadedFile('foto.pdf', JPEG_BYTES)  # extensão mente
        message = services.send_message(self.ana, self.room, file=upload)
        self.assertEqual(message.file_content_type, 'image/jpeg')
        self.assertTrue(message.is_image)
        self.assertEqual(message.file_name, 'foto.pdf')
        self.assertEqual(message.file_size, len(JPEG_BYTES))
        self.assertNotIn('foto', message.file.name)  # nome aleatório no storage

    def test_html_file_is_never_inline(self):
        message = services.send_message(self.ana, self.room, file=SimpleUploadedFile('x.jpg', HTML_BYTES))
        self.assertFalse(message.is_image)

    def test_file_name_cannot_traverse_paths(self):
        message = services.send_message(self.ana, self.room, file=SimpleUploadedFile('../../etc/passwd', b'texto'))
        self.assertEqual(message.file_name, 'passwd')

    @override_settings(CHAT_MAX_UPLOAD_MB=1)
    def test_file_size_limit_and_empty_file(self):
        with self.assertRaises(ValidationError):
            services.send_message(self.ana, self.room, file=SimpleUploadedFile('g.bin', b'0' * (1024 * 1024 + 1)))
        with self.assertRaises(ValidationError):
            services.send_message(self.ana, self.room, file=SimpleUploadedFile('v.bin', b''))
        self.assertEqual(Message.objects.count(), 0)

    def test_edit_own_text_message(self):
        message = services.send_message(self.ana, self.room, body='velho')
        edited = services.edit_message(self.ana, self.room, message.pk, ' novo ')
        self.assertEqual(edited.body, 'novo')
        self.assertIsNotNone(edited.edited_at)

    def test_cannot_edit_others_or_files_or_deleted(self):
        text = services.send_message(self.ana, self.room, body='a')
        with self.assertRaises(PermissionDenied):
            services.edit_message(self.bia, self.room, text.pk, 'sequestrada')
        file_message = services.send_message(self.ana, self.room, file=SimpleUploadedFile('a.jpg', JPEG_BYTES))
        with self.assertRaises(ValidationError):
            services.edit_message(self.ana, self.room, file_message.pk, 'x')
        services.delete_message(self.ana, self.room, text.pk)
        with self.assertRaises(ValidationError):
            services.edit_message(self.ana, self.room, text.pk, 'x')

    def test_cannot_touch_message_of_another_room(self):
        other = make_room(self.ana, name='Outra')
        message = services.send_message(self.ana, other, body='fora')
        with self.assertRaises(Message.DoesNotExist):
            services.edit_message(self.ana, self.room, message.pk, 'x')
        with self.assertRaises(Message.DoesNotExist):
            services.delete_message(self.ana, self.room, message.pk)

    def test_removed_member_cannot_edit_or_delete_old_messages(self):
        message = services.send_message(self.bia, self.room, body='minha')
        services.remove_member(self.ana, self.room, self.bia)
        with self.assertRaises(PermissionDenied):
            services.edit_message(self.bia, self.room, message.pk, 'x')
        with self.assertRaises(PermissionDenied):
            services.delete_message(self.bia, self.room, message.pk)

    def test_delete_is_soft_and_removes_file(self):
        message = services.send_message(self.ana, self.room, file=SimpleUploadedFile('a.jpg', JPEG_BYTES))
        storage, path = message.file.storage, message.file.name
        self.assertTrue(storage.exists(path))
        deleted = services.delete_message(self.ana, self.room, message.pk)
        self.assertTrue(deleted.is_deleted)
        self.assertFalse(deleted.file)
        self.assertEqual((deleted.body, deleted.file_name, deleted.file_content_type), (None, '', ''))
        self.assertFalse(storage.exists(path))
        self.assertTrue(Message.objects.filter(pk=message.pk).exists())  # linha preservada

    def test_deleting_author_user_keeps_history(self):
        message = services.send_message(self.bia, self.room, body='fica')
        self.bia.delete()
        message.refresh_from_db()
        self.assertIsNone(message.author)
        self.assertEqual(message.body, 'fica')

    def test_unread_and_mark_read(self):
        services.send_message(self.ana, self.room, body='1')
        services.send_message(self.ana, self.room, body='2')
        summary = services.room_summary(self.bia, self.room)
        self.assertEqual(summary.unread, 2)
        self.assertEqual(services.room_summary(self.ana, self.room).unread, 0)  # autor não tem não lidas
        services.mark_read(self.bia, self.room)
        self.assertEqual(services.room_summary(self.bia, self.room).unread, 0)

    def test_deleted_messages_do_not_count_as_unread(self):
        message = services.send_message(self.ana, self.room, body='apagada')
        services.delete_message(self.ana, self.room, message.pk)
        self.assertEqual(services.room_summary(self.bia, self.room).unread, 0)

    def test_mark_read_returns_messages_read_by_everyone(self):
        message = services.send_message(self.ana, self.room, body='oi')
        self.assertEqual(services.read_by_all_ids(self.room, [message]), set())
        self.assertEqual(services.mark_read(self.bia, self.room), [message.pk])
        self.assertEqual(services.read_by_all_ids(self.room, [message]), {message.pk})

    def test_read_requires_all_other_members(self):
        services.add_members(self.ana, self.room, [self.caio])
        message = services.send_message(self.ana, self.room, body='oi')
        self.assertEqual(services.mark_read(self.bia, self.room), [])  # falta o caio
        self.assertEqual(services.mark_read(self.caio, self.room), [message.pk])

    def test_non_member_cannot_mark_read(self):
        with self.assertRaises(PermissionDenied):
            services.mark_read(self.caio, self.room)

    def test_rooms_for_lists_only_my_rooms_ordered_by_activity(self):
        make_room(self.ana, self.bia, name='Quieta')
        busy = make_room(self.ana, self.bia, name='Movimentada')
        make_room(self.bia, name='Sem a Ana')
        services.send_message(self.bia, busy, body='oi')
        names = [room.name for room in services.rooms_for(self.ana)]
        self.assertEqual(names, ['Movimentada', 'Quieta', 'Equipe'])
        self.assertEqual(services.rooms_for(self.caio), [])

    def test_rooms_for_annotations(self):
        services.send_message(self.ana, self.room, body='ultima')
        summary = services.rooms_for(self.bia)[0]
        self.assertEqual(summary.member_count, 2)
        self.assertFalse(summary.is_admin)
        self.assertEqual(summary.last_message.body, 'ultima')
        self.assertTrue(services.rooms_for(self.ana)[0].is_admin)

    def test_message_page_is_chronological_with_cursor(self):
        ids = [services.send_message(self.ana, self.room, body=str(i)).pk for i in range(5)]
        page, more = services.message_page(self.room, size=2)
        self.assertEqual([m.pk for m in page], ids[3:])
        self.assertTrue(more)
        page, more = services.message_page(self.room, before_id=page[0].pk, size=2)
        self.assertEqual([m.pk for m in page], ids[1:3])
        self.assertTrue(more)
        page, more = services.message_page(self.room, before_id=page[0].pk, size=2)
        self.assertEqual([m.pk for m in page], ids[:1])
        self.assertFalse(more)


class UserSearchServiceTests(TestCase):
    def setUp(self):
        self.ana = make_user('ana')
        self.bia = make_user('bianca')
        self.beto = make_user('beto')
        make_user('inativo-bia', is_active=False)

    def test_requires_two_characters(self):
        self.assertEqual(list(services.search_users(self.ana, 'b')), [])
        self.assertEqual(list(services.search_users(self.ana, '')), [])
        self.assertEqual(list(services.search_users(self.ana, None)), [])

    def test_finds_active_users_except_self(self):
        found = list(services.search_users(self.ana, 'bi'))
        self.assertEqual(found, [self.bia])
        self.assertEqual(list(services.search_users(self.ana, 'ana')), [])

    def test_exclude_room_only_applies_to_rooms_of_the_requester(self):
        room = make_room(self.ana, self.bia)
        self.assertEqual(list(services.search_users(self.ana, 'be', exclude_room_id=room.pk)), [self.beto])
        self.assertEqual(list(services.search_users(self.ana, 'bi', exclude_room_id=room.pk)), [])
        outsider_room = make_room(self.beto, name='Dele')
        # Ana não participa: o filtro é ignorado (não revela quem está na sala).
        self.assertEqual(list(services.search_users(self.ana, 'be', exclude_room_id=outsider_room.pk)), [self.beto])
