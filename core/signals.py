from django.db.models.signals import post_save
from django.dispatch import receiver

from core.models import Group, User


@receiver(post_save, sender=User)
def assign_default_group(sender, instance, created, **kwargs):
    """Adiciona o usuário recém-criado ao grupo padrão (tipo de usuário inicial)."""
    if not created:
        return

    default_group = Group.objects.filter(is_default=True).first()

    if default_group:
        instance.groups.add(default_group)
