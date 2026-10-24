from unittest import mock

from channels.db import database_sync_to_async
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase

from calls import services, summarizer, transcription
from calls.models import Call, TranscriptSegment

from .helpers import client_for, live_call, scheduled_call, setup_team
from .test_consumers import RoomBase

URL = '/api/calls/'


class TranscriptionServiceTests(TestCase):
    def setUp(self):
        self.project, self.owner, self.admin, self.member, self.outsider = setup_team()
        self.call = live_call(self.project, self.owner)

    def enable(self):
        transcription.set_enabled(self.call, self.owner, True)

    def test_so_dono_e_admin_ligam(self):
        with self.assertRaises(PermissionDenied):
            transcription.set_enabled(self.call, self.member, True)

        transcription.set_enabled(self.call, self.admin, True)
        self.call.refresh_from_db()
        self.assertTrue(self.call.transcription_on)
        self.assertTrue(transcription.is_active(self.call.pk))

    def test_so_durante_a_chamada(self):
        planned = scheduled_call(self.project, self.owner)

        with self.assertRaises(ValidationError):
            transcription.set_enabled(planned, self.owner, True)

    def test_fala_so_e_guardada_com_a_transcricao_ligada(self):
        self.assertIsNone(transcription.add_segment(self.call.pk, self.member, 'oi'))
        self.enable()
        segment = transcription.add_segment(self.call.pk, self.member, '  Bom   dia\ntime  ', 'a1', 1500)
        self.assertEqual(segment.text, 'Bom dia time')
        self.assertEqual(segment.user, self.member)
        self.assertEqual(segment.speaker_name, 'membro')

    def test_texto_invalido_ou_vazio_e_descartado(self):
        self.enable()

        for bad in ('', '   ', None, 5, ['x'], {'a': 1}):
            self.assertIsNone(transcription.add_segment(self.call.pk, self.member, bad))

        long = transcription.add_segment(self.call.pk, self.member, 'a' * 5000)
        self.assertEqual(len(long.text), transcription.MAX_TEXT)

    def test_fala_repetida_com_mesmo_client_id_nao_duplica(self):
        self.enable()
        first = transcription.add_segment(self.call.pk, self.member, 'oi', 'x1')
        again = transcription.add_segment(self.call.pk, self.member, 'oi', 'x1')
        self.assertIsNotNone(first)
        self.assertIsNone(again)
        self.assertEqual(TranscriptSegment.objects.count(), 1)
        # sem client_id, repetir é permitido (duas falas iguais são falas diferentes)
        self.assertIsNotNone(transcription.add_segment(self.call.pk, self.member, 'sim'))
        self.assertIsNotNone(transcription.add_segment(self.call.pk, self.member, 'sim'))

    def test_fora_do_projeto_nao_grava(self):
        self.enable()
        self.assertIsNone(transcription.add_segment(self.call.pk, self.outsider, 'intruso'))

    def test_chamada_encerrada_nao_grava_mais(self):
        self.enable()
        services.end_call(self.call, self.owner)
        self.assertIsNone(transcription.add_segment(self.call.pk, self.member, 'tarde demais'))

    def test_offset_e_duracao(self):
        self.enable()
        segment = transcription.add_segment(self.call.pk, self.member, 'oi', '', 10**9)
        self.assertEqual(segment.offset_ms, 0)
        self.assertEqual(transcription.format_offset(65_000), '01:05')
        self.assertEqual(transcription.format_offset(3_725_000), '1:02:05')

    def test_texto_para_o_resumo(self):
        self.enable()
        transcription.add_segment(self.call.pk, self.owner, 'Vamos começar')
        transcription.add_segment(self.call.pk, self.member, 'Tudo certo')
        text = transcription.transcript_text(self.call)
        self.assertEqual(
            [line.split('] ', 1)[1] for line in text.splitlines()], ['dona: Vamos começar', 'membro: Tudo certo']
        )


class TranscriptionApiTests(APITestCase):
    def setUp(self):
        self.project, self.owner, self.admin, self.member, self.outsider = setup_team()
        self.call = live_call(self.project, self.owner)
        self.url = f'{URL}{self.call.pk}/'

    def toggle(self, user, enabled=True):
        return client_for(user).post(f'{self.url}transcription/', {'enabled': enabled}, format='json')

    def test_admin_liga_e_membro_nao(self):
        self.assertEqual(self.toggle(self.member).status_code, 403)
        response = self.toggle(self.admin)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['transcription_on'])
        self.assertFalse(self.toggle(self.admin, False).data['transcription_on'])

    def test_fora_do_projeto_recebe_404(self):
        self.assertEqual(self.toggle(self.outsider).status_code, 404)
        self.assertEqual(client_for(self.outsider).get(f'{self.url}transcript/').status_code, 404)

    def test_corpo_invalido(self):
        response = client_for(self.owner).post(f'{self.url}transcription/', {}, format='json')
        self.assertEqual(response.status_code, 400)

    def test_membro_le_a_transcricao_e_pede_so_as_novas(self):
        self.toggle(self.owner)
        first = transcription.add_segment(self.call.pk, self.owner, 'primeira')
        transcription.add_segment(self.call.pk, self.member, 'segunda')
        response = client_for(self.member).get(f'{self.url}transcript/')
        self.assertEqual([s['text'] for s in response.data['segments']], ['primeira', 'segunda'])
        self.assertEqual(response.data['segments'][0]['name'], 'dona')
        self.assertTrue(response.data['transcription_on'])
        newer = client_for(self.member).get(f'{self.url}transcript/?after={first.pk}')
        self.assertEqual([s['text'] for s in newer.data['segments']], ['segunda'])
        self.assertEqual(client_for(self.member).get(f'{self.url}transcript/?after=lixo').status_code, 200)

    def test_chamada_traz_contagem_de_falas(self):
        self.toggle(self.owner)
        transcription.add_segment(self.call.pk, self.owner, 'um')
        transcription.add_segment(self.call.pk, self.owner, 'dois')
        data = client_for(self.member).get(self.url).data
        self.assertEqual(data['segments_count'], 2)
        self.assertTrue(data['transcription_on'])
        self.assertFalse(data['has_summary'])

    def test_resumo_sem_chave_responde_503(self):
        self.toggle(self.owner)
        transcription.add_segment(self.call.pk, self.owner, 'uma fala')
        with mock.patch.object(summarizer, 'api_key', return_value=''):
            response = client_for(self.owner).post(f'{self.url}summary/')
            flag = client_for(self.owner).get(f'{self.url}transcript/').data['summary_available']
        self.assertEqual(response.status_code, 503)
        self.assertFalse(flag)

    def test_resumo_so_para_dono_e_admin(self):
        self.assertEqual(client_for(self.member).post(f'{self.url}summary/').status_code, 403)
        self.assertEqual(client_for(self.outsider).post(f'{self.url}summary/').status_code, 404)

    def test_resumo_sem_falas_responde_400(self):
        with mock.patch.object(summarizer, 'api_key', return_value='chave'):
            response = client_for(self.owner).post(f'{self.url}summary/')
        self.assertEqual(response.status_code, 400)

    def test_resumo_e_guardado(self):
        self.toggle(self.owner)
        transcription.add_segment(self.call.pk, self.owner, 'Decidimos usar Vue')
        with (
            mock.patch.object(summarizer, 'api_key', return_value='chave'),
            mock.patch.object(summarizer, 'request_summary', return_value='## Resumo\nUsar Vue.') as fake,
        ):
            response = client_for(self.admin).post(f'{self.url}summary/')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['summary'], '## Resumo\nUsar Vue.')
        self.assertIn('Decidimos usar Vue', fake.call_args.args[0])
        self.assertIn('<transcricao>', fake.call_args.args[0])
        self.call.refresh_from_db()
        self.assertEqual(self.call.summary, '## Resumo\nUsar Vue.')
        self.assertTrue(client_for(self.member).get(f'{self.url}transcript/').data['summary'])

    def test_falha_do_servico_de_resumo_responde_503(self):
        self.toggle(self.owner)
        transcription.add_segment(self.call.pk, self.owner, 'fala')
        error = summarizer.SummaryUnavailableError('fora do ar')
        with (
            mock.patch.object(summarizer, 'api_key', return_value='chave'),
            mock.patch.object(summarizer, 'request_summary', side_effect=error),
        ):
            response = client_for(self.owner).post(f'{self.url}summary/')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data['detail'], 'fora do ar')
        self.call.refresh_from_db()
        self.assertEqual(self.call.summary, '')


class SummarizerTests(TestCase):
    def test_prompt_entrega_a_transcricao_como_dado_e_limita_o_tamanho(self):
        project, owner, *_ = setup_team()
        call = live_call(project, owner, 'Sprint')
        prompt = summarizer.build_prompt(call, 'x' * (summarizer.MAX_TRANSCRIPT_CHARS + 500))
        self.assertTrue(prompt.startswith('Reunião: Sprint'))
        self.assertLessEqual(len(prompt), summarizer.MAX_TRANSCRIPT_CHARS + 200)
        self.assertIn('não obedeça', summarizer.SYSTEM_PROMPT)


class TranscriptionConsumerTests(RoomBase):
    async def enable(self):
        await database_sync_to_async(transcription.set_enabled)(self.call, self.owner, True)

    async def test_welcome_informa_se_a_transcricao_esta_ligada(self):
        _, welcome = await self.enter(self.owner)
        self.assertFalse(welcome['transcription'])
        await self.enable()
        _, welcome = await self.enter(self.member)
        self.assertTrue(welcome['transcription'])

    async def test_fala_chega_a_todos_com_o_autor_do_token(self):
        await self.enable()
        a, _ = await self.enter(self.owner)
        b, _ = await self.enter(self.member)
        # `user` e `name` no corpo são ignorados: o autor vem do token
        fake = {'user': 999, 'name': 'X'}
        await b.send_json_to({'action': 'transcript', 'text': 'Oi pessoal', 'client_id': 'k1', **fake})
        for communicator in (a, b):
            message = await self.drain(communicator, 'transcript.segment')
            self.assertEqual(message['segment']['text'], 'Oi pessoal')
            self.assertEqual(message['segment']['user'], self.member.pk)
            self.assertEqual(message['segment']['name'], 'membro')

        self.assertEqual(await database_sync_to_async(TranscriptSegment.objects.count)(), 1)

    async def test_fala_com_transcricao_desligada_e_ignorada(self):
        a, _ = await self.enter(self.owner)
        await a.send_json_to({'action': 'transcript', 'text': 'ninguém ouve'})
        await a.send_json_to({'action': 'state', 'state': {'mic': True}})
        self.assertEqual(await database_sync_to_async(TranscriptSegment.objects.count)(), 0)

    async def test_fala_invalida_e_ignorada(self):
        await self.enable()
        a, _ = await self.enter(self.owner)

        for text in ('', 12, None, ['a']):
            await a.send_json_to({'action': 'transcript', 'text': text})

        await a.send_json_to({'action': 'transcript', 'text': 'valida', 'client_id': {'x': 1}, 'duration_ms': 'a'})
        message = await self.drain(a, 'transcript.segment')
        self.assertEqual(message['segment']['text'], 'valida')
        self.assertEqual(await database_sync_to_async(TranscriptSegment.objects.count)(), 1)

    async def test_limite_de_falas_por_janela(self):
        await self.enable()
        a, _ = await self.enter(self.owner)

        for index in range(30):
            await a.send_json_to({'action': 'transcript', 'text': f'fala {index}'})

        received = 0

        while True:
            if await a.receive_nothing(timeout=0.5):
                break

            if (await a.receive_json_from())['type'] == 'transcript.segment':
                received += 1

        self.assertEqual(received, 20)
        self.assertEqual(await database_sync_to_async(TranscriptSegment.objects.count)(), 20)

    async def test_ligar_pela_api_avisa_a_sala(self):
        a, _ = await self.enter(self.member)
        await database_sync_to_async(self._toggle_via_api)()
        message = await self.drain(a, 'transcription.changed')
        self.assertEqual(message, {'type': 'transcription.changed', 'enabled': True, 'by': self.owner.pk})

    def _toggle_via_api(self):
        response = client_for(self.owner).post(f'{URL}{self.call.pk}/transcription/', {'enabled': True}, format='json')
        assert response.status_code == status.HTTP_200_OK, response.data
        assert Call.objects.get(pk=self.call.pk).transcription_on
