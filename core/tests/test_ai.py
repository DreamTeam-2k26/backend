import io
import json
import urllib.error
from unittest import mock

from django.conf import settings
from django.test import SimpleTestCase, override_settings

from app import settings as project_settings
from core import ai

URL = 'https://openrouter.ai/api/v1/chat/completions'


def fake_response(payload):
    response = mock.MagicMock()
    response.__enter__.return_value = io.BytesIO(json.dumps(payload).encode())
    return response


def answer(content):
    return {'choices': [{'message': {'role': 'assistant', 'content': content}}]}


def call(**extra):
    return ai.complete(
        system='sistema', prompt='pergunta', model='deepseek/deepseek-v4.1-flash', max_tokens=100, timeout=5, **extra
    )


@override_settings(AI_API_KEY='chave-teste', AI_API_URL=URL, AI_ALLOW_PAID=True)
class CompleteTests(SimpleTestCase):
    def test_without_key_raises_not_configured_and_skips_network(self):
        with override_settings(AI_API_KEY=''), mock.patch('urllib.request.urlopen') as urlopen:
            self.assertFalse(ai.is_available())
            with self.assertRaises(ai.AINotConfigured):
                call()
        urlopen.assert_not_called()

    def test_sends_openai_compatible_chat_request(self):
        with mock.patch('urllib.request.urlopen', return_value=fake_response(answer(' Olá '))) as urlopen:
            self.assertEqual(call(), 'Olá')
        (request,) = urlopen.call_args.args
        body = json.loads(request.data)
        self.assertEqual(request.full_url, URL)
        self.assertEqual(request.get_header('Authorization'), 'Bearer chave-teste')
        self.assertEqual(
            (body['model'], body['max_tokens'], body['messages']),
            (
                'deepseek/deepseek-v4.1-flash',
                100,
                [{'role': 'system', 'content': 'sistema'}, {'role': 'user', 'content': 'pergunta'}],
            ),
        )
        self.assertEqual(urlopen.call_args.kwargs['timeout'], 5)

    def test_url_is_configurable(self):
        with (
            override_settings(AI_API_URL='https://api.deepseek.com/chat/completions'),
            mock.patch('urllib.request.urlopen', return_value=fake_response(answer('ok'))) as urlopen,
        ):
            call()
        self.assertEqual(urlopen.call_args.args[0].full_url, 'https://api.deepseek.com/chat/completions')

    def test_network_errors_become_unavailable(self):
        for error in (
            urllib.error.URLError('fora'),
            TimeoutError(),
            urllib.error.HTTPError(URL, 429, 'limite', {}, None),
        ):
            with self.subTest(error=type(error).__name__), mock.patch('urllib.request.urlopen', side_effect=error):
                with self.assertRaises(ai.AIUnavailable):
                    call()

    def test_empty_error_or_invalid_answer_becomes_unavailable(self):
        payloads = (
            {'choices': []},
            answer('   '),
            answer(None),
            {'error': {'message': 'Rate limit exceeded', 'code': 429}},
            [1, 2],
        )
        for payload in payloads:
            with (
                self.subTest(payload=payload),
                mock.patch('urllib.request.urlopen', return_value=fake_response(payload)),
            ):
                with self.assertRaises(ai.AIUnavailable):
                    call()
        broken = mock.MagicMock()
        broken.__enter__.return_value = io.BytesIO(b'<html>')
        with mock.patch('urllib.request.urlopen', return_value=broken), self.assertRaises(ai.AIUnavailable):
            call()


@override_settings(AI_API_KEY='chave-teste', AI_API_URL=URL, AI_ALLOW_PAID=False)
class FreeOnlyTests(SimpleTestCase):
    def test_default_model_is_free_gemma(self):
        self.assertEqual(settings.AI_MODEL, 'google/gemma-4-31b-it:free')

    def test_paid_model_is_blocked_before_any_request(self):
        for model in ('deepseek/deepseek-v4.1-flash', 'google/gemma-4-31b-it', 'deepseek-chat'):
            with self.subTest(model=model), mock.patch('urllib.request.urlopen') as urlopen:
                with self.assertRaisesMessage(ai.AINotConfigured, 'gratuito'):
                    ai.complete(system='s', prompt='p', model=model, max_tokens=10, timeout=5)
                urlopen.assert_not_called()

    def test_free_models_are_allowed(self):
        for model in ('google/gemma-4-31b-it:free', 'openrouter/free'):
            with (
                self.subTest(model=model),
                mock.patch('urllib.request.urlopen', return_value=fake_response(answer('ok'))),
            ):
                self.assertEqual(ai.complete(system='s', prompt='p', model=model, max_tokens=10, timeout=5), 'ok')

    def test_paid_model_only_with_explicit_opt_in(self):
        with (
            override_settings(AI_ALLOW_PAID=True),
            mock.patch('urllib.request.urlopen', return_value=fake_response(answer('ok'))),
        ):
            self.assertEqual(call(), 'ok')


@override_settings(
    AI_API_KEY='chave-teste',
    AI_API_URL=URL,
    AI_ALLOW_PAID=False,
    AI_FALLBACK_MODELS=['deepseek/deepseek-v4.1-flash', 'nvidia/reserva:free'],
)
class FallbackTests(SimpleTestCase):
    def models_sent(self, urlopen):
        return [json.loads(item.args[0].data)['model'] for item in urlopen.call_args_list]

    def test_busy_free_model_falls_back_to_next_free_one_skipping_paid(self):
        busy = urllib.error.HTTPError(URL, 429, 'limite', {}, None)
        with mock.patch('urllib.request.urlopen', side_effect=[busy, fake_response(answer('ok'))]) as urlopen:
            self.assertEqual(ai.complete(system='s', prompt='p', model='google/x:free', max_tokens=5, timeout=5), 'ok')
        self.assertEqual(self.models_sent(urlopen), ['google/x:free', 'nvidia/reserva:free'])

    def test_all_models_failing_raises_unavailable(self):
        with mock.patch('urllib.request.urlopen', side_effect=urllib.error.URLError('fora')) as urlopen:
            with self.assertRaises(ai.AIUnavailable):
                ai.complete(system='s', prompt='p', model='google/x:free', max_tokens=5, timeout=5)
        self.assertEqual(self.models_sent(urlopen), ['google/x:free', 'nvidia/reserva:free'])

    def test_default_fallback_is_free(self):
        self.assertTrue(project_settings.AI_FALLBACK_MODELS)
        self.assertTrue(all(ai.is_free_model(model) for model in project_settings.AI_FALLBACK_MODELS))
