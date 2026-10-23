import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework_simplejwt.tokens import AccessToken

from chat import services
from chat.models import Room

User = get_user_model()

# JPEG mínimo válido (cabeçalho suficiente para o libmagic reconhecer image/jpeg).
JPEG_BYTES = b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9'
PNG_BYTES = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89'
    b'\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x9a\xa0\xa0\x00\x00\x00\x00IEND\xaeB`\x82'
)
PDF_BYTES = b'%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n'
HTML_BYTES = b'<html><script>alert(1)</script></html>'


def make_user(name, **extra):
    return User.objects.create_user(email=f'{name}@example.com', password='senha-forte-123', **extra)


def make_room(creator, *members, name='Equipe'):
    return services.create_room(creator, name, members)


def access_token(user):
    return str(AccessToken.for_user(user))


class TempMediaMixin:
    """Isola os arquivos enviados nos testes em um diretório temporário."""

    @classmethod
    def setUpClass(cls):
        cls._media_dir = tempfile.mkdtemp(prefix='chat-test-media-')
        cls._media_override = override_settings(MEDIA_ROOT=cls._media_dir)
        cls._media_override.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._media_override.disable()
        shutil.rmtree(cls._media_dir, ignore_errors=True)


def room_exists(room_id):
    return Room.objects.filter(pk=room_id).exists()
