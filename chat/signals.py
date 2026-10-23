"""Liga o chat ao projeto: criar/renomear/apagar projeto e mudar participantes mexem na sala."""

from django.db.models.signals import post_delete, post_save, pre_delete
from django.dispatch import receiver

from core.models import Membership as ProjectMembership
from core.models import Project

from . import project_rooms, services
from .models import Room


@receiver(post_save, sender=Project, dispatch_uid='chat_project_saved')
def project_saved(sender, instance, **kwargs):
    # raw=True: carga de fixtures; a sala vem junto no próprio dump.
    if not kwargs.get('raw'):
        project_rooms.ensure_room(instance)


@receiver(pre_delete, sender=Project, dispatch_uid='chat_project_deleting')
def project_deleting(sender, instance, **kwargs):
    # A sala e as mensagens saem em cascata; os arquivos enviados precisam ser apagados do storage à parte.
    room = Room.objects.filter(project=instance).first()

    if room is not None:
        services.delete_room_files(room)


@receiver(post_save, sender=ProjectMembership, dispatch_uid='chat_project_membership_saved')
def project_membership_saved(sender, instance, **kwargs):
    if not kwargs.get('raw'):
        project_rooms.ensure_room(instance.project)


@receiver(post_delete, sender=ProjectMembership, dispatch_uid='chat_project_membership_deleted')
def project_membership_deleted(sender, instance, **kwargs):
    # Não recria a sala: se o projeto está sendo apagado, ela some junto.
    try:
        project = instance.project
    except Project.DoesNotExist:
        return

    project_rooms.sync_room(project)
