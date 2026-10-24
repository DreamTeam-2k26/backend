"""Cliente de IA compartilhado pelo resumo de reuniões e pelo Brainstorm.

Fala o formato de chat completions compatível com a OpenAI. Por padrão usa o OpenRouter com um modelo gratuito
(`google/gemma-4-31b-it:free`). Para não haver cobrança, só modelos gratuitos (id terminado em `:free`, ou o
roteador `openrouter/free`) são chamados; um modelo pago exige `AI_ALLOW_PAID=true` de propósito.
`AI_API_URL` troca o provedor (ex.: https://api.deepseek.com/chat/completions, API oficial da DeepSeek,
com o nome de modelo dela). A chave fica só no servidor (`AI_API_KEY` no ambiente/.env). HTTPS puro (urllib),
sem dependência nova. Quem chama decide o prompt, o modelo e os limites; aqui só se faz a chamada e se traduzem
as falhas.
"""

import json
import urllib.error
import urllib.request

from django.conf import settings


class AIError(Exception):
    """Falha da IA; a mensagem é segura para mostrar ao usuário."""


class AINotConfigured(AIError):
    """Servidor sem AI_API_KEY."""


class AIUnavailable(AIError):
    """Rede, tempo esgotado, limite do provedor, erro do provedor ou resposta sem texto."""


def api_key():
    return settings.AI_API_KEY


def is_available():
    return bool(api_key())


FREE_ROUTER = 'openrouter/free'


def default_model():
    return settings.AI_MODEL


def is_free_model(model):
    return model == FREE_ROUTER or model.endswith(':free')


def complete(*, system, prompt, model, max_tokens, timeout):
    """Envia a conversa (system + user) e devolve o texto da resposta do modelo.

    Se o modelo falhar (ex.: modelo gratuito congestionado, 429), tenta os de AI_FALLBACK_MODELS, na ordem.
    Sem AI_ALLOW_PAID, modelos pagos nunca são chamados (nem como reserva).
    """
    key = api_key()
    if not key:
        raise AINotConfigured('A IA não está configurada neste servidor.')
    if not settings.AI_ALLOW_PAID and not is_free_model(model):
        # Trava contra cobrança: o pedido nem sai do servidor.
        raise AINotConfigured(
            'O modelo de IA configurado não é gratuito. Use um modelo :free ou defina AI_ALLOW_PAID=true.'
        )

    candidates = [model]
    for fallback in settings.AI_FALLBACK_MODELS:
        if fallback not in candidates and (settings.AI_ALLOW_PAID or is_free_model(fallback)):
            candidates.append(fallback)

    for candidate in candidates:
        try:
            return _request(
                key,
                timeout,
                {
                    'model': candidate,
                    'max_tokens': max_tokens,
                    'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': prompt}],
                },
            )
        except AIUnavailable as error:
            last_error = error
    raise last_error


def _request(key, timeout, payload):
    body = json.dumps(payload).encode()
    request = urllib.request.Request(  # noqa: S310 - URL https definida pelo servidor
        settings.AI_API_URL,
        data=body,
        method='POST',
        headers={'content-type': 'application/json', 'authorization': f'Bearer {key}'},
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            data = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        raise AIUnavailable('Não foi possível falar com a IA agora. Tente novamente.') from error

    # O OpenRouter pode responder 200 com {"error": ...} (limite de uso, modelo fora do ar).
    choices = data.get('choices') if isinstance(data, dict) else None
    message = choices[0].get('message') if isinstance(choices, list) and choices else None
    text = message.get('content') if isinstance(message, dict) else None

    if not isinstance(text, str) or not text.strip():
        raise AIUnavailable('A IA não devolveu texto. Tente novamente.')

    return text.strip()
