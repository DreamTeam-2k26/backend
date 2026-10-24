"""Configurações das chamadas (todas opcionais; valores padrão servem para desenvolvimento)."""

import json
import os

from django.conf import settings

DEFAULT_ICE_SERVERS = [{'urls': ['stun:stun.l.google.com:19302', 'stun:stun1.l.google.com:19302']}]


def max_participants():
    """Limite por chamada: a conexão é direta entre todos (malha), então o custo cresce com o quadrado."""
    return int(getattr(settings, 'CALL_MAX_PARTICIPANTS', os.environ.get('CALL_MAX_PARTICIPANTS', '8')))


def ice_servers():
    """Servidores STUN/TURN enviados aos clientes. Para redes restritas, defina CALL_ICE_SERVERS (JSON)."""
    configured = getattr(settings, 'CALL_ICE_SERVERS', None) or os.environ.get('CALL_ICE_SERVERS')

    if not configured:
        return DEFAULT_ICE_SERVERS

    try:
        servers = json.loads(configured) if isinstance(configured, str) else configured
    except ValueError:
        return DEFAULT_ICE_SERVERS

    return servers if isinstance(servers, list) and servers else DEFAULT_ICE_SERVERS
