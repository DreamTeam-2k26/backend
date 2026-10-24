"""Login com Google: verificação do ID token do Google Identity Services e vínculo com usuários do Fluxo.

Fluxo: o frontend obtém `response.credential` (ID token) do GIS e envia aqui. O token é conferido no servidor
pela biblioteca oficial google-auth (assinatura com as chaves públicas do Google, aud = GOOGLE_CLIENT_ID, iss,
exp). A identidade é sempre o `sub`; e-mail e nome são só dados de perfil. O ID token do Google nunca vira
token da API: quem entra recebe o mesmo JWT do login tradicional.
"""

import io
import logging
import re
from urllib.parse import urlsplit

import requests
from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.files.base import ContentFile
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.utils import timezone
from google.auth import exceptions as google_exceptions
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.serializers import BooleanField, CharField, EmailField, Serializer

from core.models import GoogleIdentity, User
from uploader.helpers.files import CONTENT_TYPE_JPG, CONTENT_TYPE_PNG, get_content_type
from uploader.models import Image

logger = logging.getLogger(__name__)

# Tolerância para relógios levemente fora de sincronia entre o servidor e o Google.
CLOCK_SKEW_SECONDS = 10
NAME_MAX = User._meta.get_field('name').max_length

# Reaproveita a sessão HTTP usada para buscar as chaves públicas do Google entre os logins.
_google_request = google_requests.Request()


class GoogleAuthError(APIException):
    """Erro do login com Google no formato {"detail": mensagem, "code": código}.

    Mensagens são seguras para o usuário final e nunca repetem o token recebido.
    """

    def __init__(self, status_code, code, detail):
        self.status_code = status_code
        super().__init__({'detail': detail, 'code': code})


def unavailable():
    return GoogleAuthError(
        status.HTTP_503_SERVICE_UNAVAILABLE, 'google_auth_unavailable', 'O login com Google não está disponível.'
    )


def invalid_token():
    return GoogleAuthError(
        status.HTTP_401_UNAUTHORIZED,
        'invalid_google_token',
        'Não foi possível validar a conta Google. Entre com o Google novamente.',
    )


def email_not_verified():
    return GoogleAuthError(
        status.HTTP_403_FORBIDDEN,
        'google_email_not_verified',
        'A conta Google precisa ter um e-mail verificado.',
    )


def google_outage():
    return GoogleAuthError(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        'google_unavailable',
        'Não foi possível falar com o Google agora. Tente novamente em instantes.',
    )


def inactive_account():
    return GoogleAuthError(status.HTTP_403_FORBIDDEN, 'inactive_account', 'Esta conta está desativada.')


def account_link_required():
    return GoogleAuthError(
        status.HTTP_409_CONFLICT,
        'account_link_required',
        'Já existe uma conta com este e-mail. Entre com e-mail e senha e vincule o Google no seu perfil.',
    )


def google_account_in_use():
    return GoogleAuthError(
        status.HTTP_409_CONFLICT,
        'google_account_in_use',
        'Esta conta Google já está vinculada a outro usuário do Fluxo.',
    )


def already_linked():
    return GoogleAuthError(
        status.HTTP_409_CONFLICT, 'already_linked', 'Sua conta já está vinculada a outra conta Google.'
    )


def verify_google_credential(credential):
    """Confere o ID token no servidor e devolve as claims; lança GoogleAuthError se não puder confiar nele."""
    if not settings.GOOGLE_CLIENT_ID:
        raise unavailable()
    try:
        claims = id_token.verify_oauth2_token(
            credential, _google_request, settings.GOOGLE_CLIENT_ID, clock_skew_in_seconds=CLOCK_SKEW_SECONDS
        )
    except google_exceptions.TransportError as error:
        logger.warning('Falha ao buscar as chaves públicas do Google (%s).', type(error).__name__)
        raise google_outage() from error
    except (ValueError, google_exceptions.GoogleAuthError) as error:
        # Assinatura, aud, exp, formato (ValueError) ou iss (GoogleAuthError). Nunca registra o token.
        logger.info('ID token do Google recusado (%s).', type(error).__name__)
        raise invalid_token() from error

    sub = claims.get('sub')
    if not isinstance(sub, str) or not sub.strip():
        raise invalid_token()

    email = claims.get('email')
    if claims.get('email_verified') not in {True, 'true'} or not isinstance(email, str):
        raise email_not_verified()
    try:
        validate_email(email)
    except DjangoValidationError as error:
        raise email_not_verified() from error
    return claims


# Foto da conta Google: só https em *.googleusercontent.com, sem seguir redirecionamentos, com tempo e tamanho
# limitados e conteúdo conferido (JPEG/PNG, como a foto de perfil enviada pelo uploader).
PICTURE_HOST_SUFFIX = '.googleusercontent.com'
PICTURE_SIZE = 256
PICTURE_TIMEOUT = (3, 5)  # segundos para conectar e para ler
PICTURE_EXTENSIONS = {CONTENT_TYPE_JPG: '.jpg', CONTENT_TYPE_PNG: '.png'}
_picture_session = requests.Session()


def google_picture_url(claims):
    """URL da foto se for do Google por https; qualquer outra coisa é ignorada (sem requisição a hosts arbitrários)."""
    url = claims.get('picture')
    if not isinstance(url, str):
        return None
    parts = urlsplit(url)
    if parts.scheme != 'https' or not (parts.hostname or '').endswith(PICTURE_HOST_SUFFIX):
        return None
    return url


def download_google_picture(url):
    """Bytes da foto (em 256px) ou None se o Google não entregar uma imagem válida dentro dos limites."""
    sized_url = re.sub(r'=s\d+(-c)?$', f'=s{PICTURE_SIZE}-c', url)
    try:
        response = _picture_session.get(sized_url, timeout=PICTURE_TIMEOUT, stream=True, allow_redirects=False)
    except (requests.RequestException, OSError) as error:
        logger.warning('Não foi possível baixar a foto do Google (%s).', type(error).__name__)
        return None
    try:
        if response.status_code != status.HTTP_200_OK:
            return None
        content = b''
        for chunk in response.iter_content(64 * 1024):
            content += chunk
            if len(content) > settings.MAX_UPLOAD_SIZE:
                return None
        return content or None
    finally:
        response.close()


def sync_google_picture(identity, user, claims):
    """Usa a foto do Google como foto de perfil quando a pessoa não escolheu outra no Fluxo.

    - Já tem uma foto enviada por ela: nunca é trocada.
    - A foto atual veio do Google e o Google mudou: atualiza.
    - Ela removeu a foto do Google: só volta se o Google tiver uma foto nova.
    Qualquer falha (rede, storage, arquivo inválido) só deixa sem foto: o login nunca depende disso.
    """
    url = google_picture_url(claims)
    has_own_photo = user.profile_picture_id is not None and user.profile_picture_id != identity.picture_id
    if url is None or has_own_photo or url == identity.picture_url:
        return
    try:
        store_google_picture(identity, user, url)
    except Exception:  # noqa: BLE001 - foto é acessório: falha de storage não pode impedir o login
        logger.exception('Falha ao importar a foto do Google do usuário %s.', user.pk)


def store_google_picture(identity, user, url):
    """Baixa, confere e grava a foto como foto de perfil; a foto anterior vinda do Google é apagada."""
    content = download_google_picture(url)
    content_type = get_content_type(io.BytesIO(content)) if content else None
    if content_type not in PICTURE_EXTENSIONS:
        return
    previous = identity.picture
    with transaction.atomic():
        image = Image.objects.create(
            file=ContentFile(content, name=f'google{PICTURE_EXTENSIONS[content_type]}'),
            description='Foto da conta Google',
            uploaded_by=user,
        )
        user.profile_picture = image
        user.save(update_fields=['profile_picture'])
        identity.picture = image
        identity.picture_url = url
        identity.save(update_fields=['picture', 'picture_url'])
    if previous is not None:
        # A foto antiga do Google não é mais usada por ninguém: some do banco e do storage.
        previous.file.delete(save=False)
        previous.delete()


def display_name(claims):
    """Nome do perfil: name, senão given + family, senão a parte do e-mail antes do @."""
    name = (claims.get('name') or '').strip()
    if not name:
        name = ' '.join(part for part in (claims.get('given_name'), claims.get('family_name')) if part).strip()
    return (name or claims['email'].split('@', 1)[0])[:NAME_MAX]


class GoogleCredentialSerializer(Serializer):
    credential = CharField(
        max_length=8192, help_text='ID token do Google: o `response.credential` entregue pelo Google Identity Services.'
    )

    def validate_credential(self, credential):
        self.claims = verify_google_credential(credential)
        return credential


class GoogleLoginSerializer(GoogleCredentialSerializer):
    """Entra com a conta vinculada ao `sub` ou cria um usuário novo (nunca toma conta existente pelo e-mail)."""

    def find_identity(self, sub):
        return GoogleIdentity.objects.select_related('user').filter(google_sub=sub).first()

    def create_user(self, sub, email):
        """Cria usuário + vínculo numa transação. None se o e-mail já pertence a alguém."""
        if User.objects.filter(email__iexact=email).exists():
            return None
        with transaction.atomic():
            # password=None: senha inutilizável (só entra pelo Google). O signal põe no grupo padrão.
            user = User.objects.create_user(email=email, password=None, name=display_name(self.claims))
            GoogleIdentity.objects.create(user=user, google_sub=sub, email=email, last_login_at=timezone.now())
        return user

    def save(self, **kwargs):
        sub, email = self.claims['sub'], self.claims['email']
        identity = self.find_identity(sub)

        if identity is None:
            try:
                user = self.create_user(sub, email)
            except IntegrityError:
                # Outra requisição criou o mesmo vínculo (ou o mesmo e-mail) ao mesmo tempo.
                user = None
            if user is not None:
                sync_google_picture(user.google_identity, user, self.claims)
                return user
            identity = self.find_identity(sub)
            if identity is None:
                raise account_link_required()

        if not identity.user.is_active:
            raise inactive_account()
        identity.email = email
        identity.last_login_at = timezone.now()
        identity.save(update_fields=['email', 'last_login_at'])
        sync_google_picture(identity, identity.user, self.claims)
        return identity.user


class GoogleLinkSerializer(GoogleCredentialSerializer):
    """Vincula a conta Google ao usuário do JWT (que já provou ser dono da conta Fluxo)."""

    def save(self, **kwargs):
        user = self.context['request'].user
        identity = self.link(user)
        sync_google_picture(identity, user, self.claims)
        return identity

    def link(self, user):
        sub, email = self.claims['sub'], self.claims['email']

        for _attempt in range(2):
            current = GoogleIdentity.objects.filter(google_sub=sub).first()
            if current is not None:
                if current.user_id != user.pk:
                    raise google_account_in_use()
                return current
            if GoogleIdentity.objects.filter(user=user).exists():
                raise already_linked()
            try:
                with transaction.atomic():
                    return GoogleIdentity.objects.create(user=user, google_sub=sub, email=email)
            except IntegrityError:
                continue  # corrida com outro vínculo: reavalia quem ficou com o sub
        raise google_account_in_use()


class GoogleTokenPairSerializer(Serializer):
    """Mesmo par de JWT do POST /api/token/."""

    access = CharField()
    refresh = CharField()


class GoogleLinkStatusSerializer(Serializer):
    linked = BooleanField()
    email = EmailField(allow_null=True, help_text='E-mail da conta Google vinculada (null se não houver).')


class GoogleAuthErrorSerializer(Serializer):
    detail = CharField()
    code = CharField(
        help_text='invalid_google_token, google_email_not_verified, inactive_account, account_link_required, '
        'google_account_in_use, already_linked, google_unavailable, google_auth_unavailable'
    )
