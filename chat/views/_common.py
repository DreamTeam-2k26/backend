from contextlib import contextmanager

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import NotFound
from rest_framework.exceptions import ValidationError as DRFValidationError

from chat.models import Message


@contextmanager
def domain_errors():
    """Traduz os erros de domínio (services) para respostas HTTP.

    PermissionDenied do Django já vira 403 pelo handler padrão do DRF.
    """
    try:
        yield
    except DjangoValidationError as error:
        raise DRFValidationError({'detail': ' '.join(error.messages)}) from error
    except Message.DoesNotExist as error:
        raise NotFound('Mensagem não encontrada.') from error
