import mimetypes

import magic
from django.conf import settings
from rest_framework import serializers

CONTENT_TYPE_JPG = 'image/jpeg'
CONTENT_TYPE_PNG = 'image/png'

CONTENT_TYPE_PDF = 'application/pdf'

# Bytes iniciais suficientes para o libmagic identificar o tipo do arquivo.
MAGIC_HEADER_SIZE = 2048


def get_content_type(file):
    if hasattr(file, 'temporary_file_path'):
        content_type = magic.from_file(file.temporary_file_path(), mime=True)
    else:
        content_type = magic.from_buffer(file.read(MAGIC_HEADER_SIZE), mime=True)

    if hasattr(file, 'seek') and callable(file.seek):
        file.seek(0)

    return content_type


def get_extension(file) -> str:
    extension = mimetypes.guess_extension(get_content_type(file)) or ''

    return '.jpg' if extension == '.jpe' else extension


def validate_upload(file, valid_content_types, error_message):
    """Valida o tamanho e o tipo real (pelo conteúdo) de um arquivo enviado."""
    max_size = settings.MAX_UPLOAD_SIZE

    if file.size > max_size:
        raise serializers.ValidationError(f'O arquivo excede o tamanho máximo de {max_size // (1024 * 1024)} MB.')

    if get_content_type(file) not in valid_content_types:
        raise serializers.ValidationError(error_message)

    return file
