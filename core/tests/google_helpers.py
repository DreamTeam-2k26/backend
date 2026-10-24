"""Tokens do Google assinados de verdade com uma chave RSA de teste.

Só o download das chaves públicas do Google (google.oauth2.id_token._fetch_certs) é substituído: a
assinatura, o aud, o iss e o exp continuam sendo conferidos pela própria biblioteca google-auth, sem rede.
"""

import time
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import override_settings
from google.auth import crypt, jwt

CLIENT_ID = 'fluxo-teste.apps.googleusercontent.com'
KEY_ID = 'fluxo-chave-de-teste'
GOOGLE_SUB = '109876543210987654321'


def _new_key():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return private_pem, public_pem


GOOGLE_PRIVATE_KEY, GOOGLE_PUBLIC_KEY = _new_key()
ATTACKER_PRIVATE_KEY, _ = _new_key()


def google_credential(private_key=GOOGLE_PRIVATE_KEY, **claims):
    """ID token como o GIS entrega ao frontend. Passe claim=None para removê-la."""
    now = int(time.time())
    payload = {
        'iss': 'https://accounts.google.com',
        'aud': CLIENT_ID,
        'sub': GOOGLE_SUB,
        'email': 'ana.souza@gmail.com',
        'email_verified': True,
        'name': 'Ana Souza',
        'iat': now,
        'exp': now + 3600,
        **claims,
    }
    payload = {key: value for key, value in payload.items() if value is not None}
    signer = crypt.RSASigner.from_string(private_key, key_id=KEY_ID)
    return jwt.encode(signer, payload).decode()


class GoogleAuthMixin:
    """Configura o Client ID de teste e entrega as chaves públicas de teste no lugar das do Google."""

    def setUp(self):
        super().setUp()
        settings_override = override_settings(GOOGLE_CLIENT_ID=CLIENT_ID)
        settings_override.enable()
        self.addCleanup(settings_override.disable)
        certs = mock.patch('google.oauth2.id_token._fetch_certs', return_value={KEY_ID: GOOGLE_PUBLIC_KEY})
        self.fetch_certs = certs.start()
        self.addCleanup(certs.stop)
