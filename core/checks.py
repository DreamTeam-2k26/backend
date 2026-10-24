from django.conf import settings
from django.core.checks import Warning, register


@register()
def check_google_client_id(app_configs, **kwargs):
    """Avisa (sem derrubar o servidor) quando o login com Google está sem Client ID em produção."""
    if settings.DEBUG or settings.GOOGLE_CLIENT_ID:
        return []
    return [
        Warning(
            'GOOGLE_CLIENT_ID não está definido: /api/auth/google/ vai responder 503.',
            hint='Defina GOOGLE_CLIENT_ID no ambiente (veja "Login com Google" no README).',
            id='core.W001',
        )
    ]
