import logging

from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from core.models import File, Group, User

logger = logging.getLogger(__name__)


@receiver(post_save, sender=User)
def assign_default_group(sender, instance, created, **kwargs):
    """Adiciona o usuário recém-criado ao grupo padrão (tipo de usuário inicial)."""
    if not created:
        return

    default_group = Group.objects.filter(is_default=True).first()

    if default_group:
        instance.groups.add(default_group)


@receiver(post_delete, sender=File)
def remove_project_file_from_storage(sender, instance, **kwargs):
    """Apaga o conteúdo do storage quando o registro some (exclusão direta ou cascata de pasta/projeto).

    Só depois do commit: se a transação for desfeita, o arquivo continua lá. Falha do storage não derruba nada.
    """
    stored = instance.file

    def remove():
        try:
            stored.delete(save=False)
        except Exception:  # noqa: BLE001 - falha de storage não pode quebrar a exclusão já confirmada
            logger.exception('Falha ao remover do storage o arquivo %s', stored.name)

    if stored:
        transaction.on_commit(remove)
