from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APIClient, APITestCase

from chat import services
from chat.models import Membership, Message, Room

from .helpers import HTML_BYTES, JPEG_BYTES, PDF_BYTES, TempMediaMixin, make_room, make_user

ROOMS = '/api/chat/rooms/'


def room_url(room, suffix=''):
    return f'{ROOMS}{room.pk}/{suffix}'


class ChatAPITestCase(TempMediaMixin, APITestCase):
    def setUp(self):
        self.ana = make_user('ana')
        self.bia = make_user('bia')
        self.caio = make_user('caio')
        self.room = make_room(self.ana, self.bia, name='Projeto')
        self.client_for = {}

    def login(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def as_(self, user):
        return self.login(user)


class AuthenticationTests(ChatAPITestCase):
    def test_all_endpoints_require_authentication(self):
        message = services.send_message(self.ana, self.room, body='oi')
        anonymous = APIClient()
        requests = [
            ('get', ROOMS),
            ('get', room_url(self.room)),
            ('post', room_url(self.room, 'read/')),
            ('get', room_url(self.room, 'messages/')),
            ('post', room_url(self.room, 'messages/')),
            ('get', f'/api/chat/messages/{message.pk}/file/'),
            ('get', '/api/chat/users/?search=an'),
        ]
        for method, url in requests:
            with self.subTest(method=method, url=url):
                self.assertEqual(getattr(anonymous, method)(url).status_code, 401)

    def test_jwt_token_authenticates(self):
        client = APIClient()
        response = client.post('/api/token/', {'email': self.ana.email, 'password': 'senha-forte-123'})
        self.assertEqual(response.status_code, 200)
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {response.data["access"]}')
        self.assertEqual(client.get(ROOMS).status_code, 200)

    def test_regular_user_without_model_permissions_can_use_chat(self):
        # Usuário recém-criado não tem permissão de modelo do chat (o grupo padrão só dá o básico, como enviar
        # foto); o chat autoriza por participação.
        self.assertFalse({perm for perm in self.caio.get_all_permissions() if perm.startswith('chat.')})
        self.assertEqual(self.as_(self.caio).get(ROOMS).status_code, 200)


class RoomAPITests(ChatAPITestCase):
    def test_list_returns_only_my_rooms(self):
        make_room(self.caio, name='Do Caio')
        response = self.as_(self.ana).get(ROOMS)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([room['name'] for room in response.data['results']], ['Projeto'])
        self.assertEqual(self.as_(self.caio).get(ROOMS).data['count'], 1)

    def test_list_shape_and_pagination(self):
        for i in range(12):
            make_room(self.ana, name=f'S{i}')
        first = self.as_(self.ana).get(ROOMS).data
        self.assertEqual((first['page'], first['page_size'], first['count']), (1, 10, 13))
        everything = self.as_(self.ana).get(ROOMS, {'page_size': 100}).data
        self.assertEqual(len(everything['results']), 13)
        room = everything['results'][0]
        for key in ('id', 'name', 'image', 'member_count', 'unread', 'is_admin', 'project', 'last_message', 'last_message_at'):
            self.assertIn(key, room)

    def test_rooms_are_not_created_or_managed_through_the_api(self):
        client = self.as_(self.ana)
        self.assertEqual(client.post(ROOMS, {'name': 'Nova'}, format='json').status_code, 405)
        self.assertEqual(client.patch(room_url(self.room), {'name': 'Hack'}, format='json').status_code, 405)
        self.assertEqual(client.delete(room_url(self.room)).status_code, 405)
        for method, suffix in (
            ('post', 'leave/'),
            ('post', 'members/'),
            ('delete', f'members/{self.bia.pk}/'),
            ('post', f'members/{self.bia.pk}/admin/'),
        ):
            with self.subTest(method=method, suffix=suffix):
                response = getattr(client, method)(room_url(self.room, suffix), {'users': [self.caio.pk]}, format='json')
                self.assertEqual(response.status_code, 404)
        self.assertEqual(Membership.objects.filter(room=self.room).count(), 2)
        self.assertEqual(Room.objects.count(), 1)

    def test_there_is_no_direct_message_endpoint(self):
        for url in ('/api/chat/conversations/', '/api/chat/direct/', '/api/chat/start/'):
            self.assertEqual(self.as_(self.ana).post(url, {'user': self.bia.pk}, format='json').status_code, 404)

    def test_non_member_gets_404_everywhere(self):
        message = services.send_message(self.ana, self.room, body='segredo')
        client = self.as_(self.caio)
        checks = [
            ('get', room_url(self.room)),
            ('post', room_url(self.room, 'read/')),
            ('get', room_url(self.room, 'messages/')),
            ('post', room_url(self.room, 'messages/')),
            ('get', f'/api/chat/messages/{message.pk}/file/'),
        ]
        for method, url in checks:
            with self.subTest(method=method, url=url):
                response = getattr(client, method)(url, {'body': 'x', 'users': [self.caio.pk], 'name': 'x'}, format='json')
                self.assertEqual(response.status_code, 404)
        # e nada mudou
        self.assertEqual(Message.objects.count(), 1)
        self.assertEqual(Membership.objects.filter(room=self.room).count(), 2)
        self.room.refresh_from_db()
        self.assertEqual(self.room.name, 'Projeto')

    def test_nonexistent_room_looks_the_same_as_forbidden(self):
        forbidden = self.as_(self.caio).get(room_url(self.room))
        missing = self.as_(self.caio).get(f'{ROOMS}999999/')
        self.assertEqual(forbidden.status_code, missing.status_code)
        self.assertEqual(forbidden.data, missing.data)

    def test_member_can_read_detail(self):
        response = self.as_(self.bia).get(room_url(self.room))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['name'], 'Projeto')
        self.assertFalse(response.data['is_admin'])


class MessageAPITests(ChatAPITestCase):
    def test_history_is_paginated_with_cursor(self):
        ids = [services.send_message(self.ana, self.room, body=f'm{i}').pk for i in range(35)]
        client = self.as_(self.bia)
        first = client.get(room_url(self.room, 'messages/')).data
        self.assertEqual([m['id'] for m in first['results']], ids[5:])
        self.assertTrue(first['has_more'])
        self.assertEqual(first['next_before'], ids[5])
        second = client.get(room_url(self.room, 'messages/'), {'before': first['next_before']}).data
        self.assertEqual([m['id'] for m in second['results']], ids[:5])
        self.assertFalse(second['has_more'])
        self.assertIsNone(second['next_before'])

    def test_history_survives_reload_and_is_chronological(self):
        services.send_message(self.ana, self.room, body='primeira')
        services.send_message(self.bia, self.room, body='segunda')
        results = self.as_(self.ana).get(room_url(self.room, 'messages/')).data['results']
        self.assertEqual([m['body'] for m in results], ['primeira', 'segunda'])
        self.assertEqual(results[0]['author']['id'], self.ana.pk)
        self.assertEqual(results[1]['author']['name'], 'bia')

    def test_history_rejects_bad_cursor(self):
        response = self.as_(self.ana).get(room_url(self.room, 'messages/'), {'before': 'abc'})
        self.assertEqual(response.status_code, 400)

    def test_history_of_another_room_is_not_mixed_in(self):
        other = make_room(self.ana, name='Outra')
        services.send_message(self.ana, other, body='outra sala')
        services.send_message(self.ana, self.room, body='desta sala')
        bodies = [m['body'] for m in self.as_(self.bia).get(room_url(self.room, 'messages/')).data['results']]
        self.assertEqual(bodies, ['desta sala'])

    def test_message_payload_shape(self):
        original = services.send_message(self.ana, self.room, body='base')
        services.send_message(self.bia, self.room, body='resposta', reply_to_id=original.pk)
        results = self.as_(self.ana).get(room_url(self.room, 'messages/')).data['results']
        reply = results[1]
        for key in ('id', 'room', 'author', 'body', 'file', 'created', 'edited_at', 'is_deleted', 'reply_to', 'read'):
            self.assertIn(key, reply)
        self.assertEqual(reply['reply_to']['id'], original.pk)
        self.assertEqual(reply['reply_to']['body'], 'base')
        self.assertEqual(reply['room'], self.room.pk)

    def test_send_text_via_rest(self):
        response = self.as_(self.ana).post(room_url(self.room, 'messages/'), {'body': ' oi '}, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['body'], 'oi')
        self.assertEqual(response.data['author']['id'], self.ana.pk)
        self.assertEqual(Message.objects.get().author, self.ana)

    def test_author_comes_from_the_token_not_from_the_payload(self):
        response = self.as_(self.ana).post(
            room_url(self.room, 'messages/'), {'body': 'oi', 'author': self.bia.pk, 'author_id': self.bia.pk}, format='json'
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Message.objects.get().author, self.ana)

    def test_send_validation(self):
        client = self.as_(self.ana)
        for payload in ({}, {'body': ''}, {'body': '   '}, {'body': 'x' * 2001}, {'body': 'x', 'reply_to': 999999}):
            with self.subTest(payload=payload):
                self.assertEqual(client.post(room_url(self.room, 'messages/'), payload, format='json').status_code, 400)
        self.assertEqual(Message.objects.count(), 0)

    def test_send_file_via_multipart_and_download(self):
        upload = SimpleUploadedFile('relatorio.pdf', PDF_BYTES, content_type='application/pdf')
        response = self.as_(self.ana).post(room_url(self.room, 'messages/'), {'file': upload}, format='multipart')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['file'], {
            'name': 'relatorio.pdf', 'size': len(PDF_BYTES), 'content_type': 'application/pdf', 'is_image': False,
        })
        self.assertNotIn('url', response.data['file'])  # o caminho do storage nunca sai
        download = self.as_(self.bia).get(f'/api/chat/messages/{response.data["id"]}/file/')
        self.assertEqual(download.status_code, 200)
        self.assertEqual(b''.join(download.streaming_content), PDF_BYTES)
        self.assertIn('attachment', download['Content-Disposition'])
        self.assertIn('relatorio.pdf', download['Content-Disposition'])
        self.assertEqual(download['Content-Type'], 'application/octet-stream')
        self.assertEqual(download['X-Content-Type-Options'], 'nosniff')

    def test_image_is_served_inline_with_real_content_type(self):
        upload = SimpleUploadedFile('foto.jpg', JPEG_BYTES)
        sent = self.as_(self.ana).post(room_url(self.room, 'messages/'), {'file': upload}, format='multipart')
        self.assertTrue(sent.data['file']['is_image'])
        download = self.as_(self.bia).get(f'/api/chat/messages/{sent.data["id"]}/file/')
        self.assertEqual(download['Content-Type'], 'image/jpeg')
        self.assertIn('inline', download['Content-Disposition'])

    def test_html_disguised_as_image_is_never_rendered(self):
        upload = SimpleUploadedFile('inocente.png', HTML_BYTES, content_type='image/png')
        sent = self.as_(self.ana).post(room_url(self.room, 'messages/'), {'file': upload}, format='multipart')
        self.assertFalse(sent.data['file']['is_image'])
        download = self.as_(self.bia).get(f'/api/chat/messages/{sent.data["id"]}/file/')
        self.assertEqual(download['Content-Type'], 'application/octet-stream')
        self.assertIn('attachment', download['Content-Disposition'])

    def test_file_download_denied_to_non_members_and_after_removal(self):
        upload = SimpleUploadedFile('a.pdf', PDF_BYTES)
        sent = self.as_(self.ana).post(room_url(self.room, 'messages/'), {'file': upload}, format='multipart')
        url = f'/api/chat/messages/{sent.data["id"]}/file/'
        self.assertEqual(self.as_(self.caio).get(url).status_code, 404)
        self.assertEqual(self.as_(self.bia).get(url).status_code, 200)
        services.remove_member(self.ana, self.room, self.bia)
        self.assertEqual(self.as_(self.bia).get(url).status_code, 404)

    def test_deleted_or_missing_file_returns_404(self):
        sent = self.as_(self.ana).post(
            room_url(self.room, 'messages/'), {'file': SimpleUploadedFile('a.pdf', PDF_BYTES)}, format='multipart'
        )
        url = f'/api/chat/messages/{sent.data["id"]}/file/'
        services.delete_message(self.ana, self.room, sent.data['id'])
        self.assertEqual(self.as_(self.bia).get(url).status_code, 404)
        text = services.send_message(self.ana, self.room, body='sem arquivo')
        self.assertEqual(self.as_(self.bia).get(f'/api/chat/messages/{text.pk}/file/').status_code, 404)
        self.assertEqual(self.as_(self.bia).get('/api/chat/messages/999999/file/').status_code, 404)

    def test_file_without_body_and_with_body_is_rejected(self):
        both = {'body': 'oi', 'file': SimpleUploadedFile('a.pdf', PDF_BYTES)}
        response = self.as_(self.ana).post(room_url(self.room, 'messages/'), both, format='multipart')
        self.assertEqual(response.status_code, 400)

    @override_settings(CHAT_MAX_UPLOAD_MB=1)
    def test_oversized_file_is_rejected(self):
        big = SimpleUploadedFile('grande.bin', b'0' * (1024 * 1024 + 1))
        response = self.as_(self.ana).post(room_url(self.room, 'messages/'), {'file': big}, format='multipart')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Message.objects.count(), 0)

    def test_mark_read(self):
        services.send_message(self.ana, self.room, body='oi')
        self.assertEqual(self.as_(self.bia).get(room_url(self.room)).data['unread'], 1)
        self.assertEqual(self.as_(self.bia).post(room_url(self.room, 'read/')).status_code, 204)
        self.assertEqual(self.as_(self.bia).get(room_url(self.room)).data['unread'], 0)
        results = self.as_(self.ana).get(room_url(self.room, 'messages/')).data['results']
        self.assertTrue(results[0]['read'])


class UserSearchAPITests(ChatAPITestCase):
    def setUp(self):
        super().setUp()
        self.beto = make_user('beto')

    def search(self, user, **params):
        return self.as_(user).get('/api/chat/users/', params)

    def test_result_exposes_only_public_fields(self):
        response = self.search(self.ana, search='be')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['results'], [{'id': self.beto.pk, 'name': 'beto', 'avatar': None}])

    def test_short_or_missing_term_returns_nothing(self):
        self.assertEqual(self.search(self.ana, search='b').data['count'], 0)
        self.assertEqual(self.search(self.ana).data['count'], 0)

    def test_excludes_self_and_inactive(self):
        make_user('betina', is_active=False)
        names = [u['name'] for u in self.search(self.ana, search='be').data['results']]
        self.assertEqual(names, ['beto'])
        self.assertEqual(self.search(self.ana, search='ana').data['count'], 0)

    def test_exclude_room(self):
        names = [u['name'] for u in self.search(self.ana, search='example', exclude_room=self.room.pk).data['results']]
        self.assertEqual(sorted(names), ['beto', 'caio'])

    def test_bad_exclude_room(self):
        self.assertEqual(self.search(self.ana, search='be', exclude_room='x').status_code, 400)
