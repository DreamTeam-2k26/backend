"""Sala de conversa de cada projeto: criada com o projeto e com os mesmos participantes.

A fonte de verdade é o projeto (core.Project / core.Membership). Os sinais de chat/signals.py chamam
estas funções, então quem entra, sai ou muda de papel no projeto entra, sai ou muda na sala sozinho.
Funções síncronas e idempotentes: chamar de novo não muda nada.
"""

from django.db import transaction

from core.models import Membership as ProjectMembership

from . import realtime
from .models import Membership, Room
from .models.room import ROOM_NAME_MAX

ADMIN_ROLES = (ProjectMembership.Role.OWNER, ProjectMembership.Role.ADMIN)


def _broadcast_on_commit(room_id, user_ids, removed_user_ids=()):
    transaction.on_commit(
        lambda: realtime.broadcast_room_changed(room_id, [*user_ids, *removed_user_ids], list(removed_user_ids))
    )


def ensure_room(project):
    """Sala do projeto (cria se ainda não existir) com nome e participantes sincronizados."""
    room, _ = Room.objects.get_or_create(
        project=project,
        defaults={'name': project.name[:ROOM_NAME_MAX], 'created_by': project.created_by},
    )
    sync_room(project, room)

    return room


def sync_room(project, room=None):
    """Alinha nome e participantes da sala ao projeto e avisa em tempo real se algo mudou."""
    room = room or Room.objects.filter(project=project).first()

    if room is None:
        return None

    changed = False
    name = project.name[:ROOM_NAME_MAX]

    if room.name != name:
        room.name = name
        room.save(update_fields=['name'])
        changed = True

    desired = {
        membership.user_id: membership.role in ADMIN_ROLES
        for membership in ProjectMembership.objects.filter(project=project, user__is_active=True)
    }
    current = {membership.user_id: membership for membership in Membership.objects.filter(room=room)}

    removed = [user_id for user_id in current if user_id not in desired]
    added = [user_id for user_id in desired if user_id not in current]

    if removed:
        Membership.objects.filter(room=room, user_id__in=removed).delete()

    if added:
        Membership.objects.bulk_create([Membership(room=room, user_id=uid, is_admin=desired[uid]) for uid in added])

    for user_id, is_admin in desired.items():
        membership = current.get(user_id)

        if membership is not None and membership.is_admin != is_admin:
            Membership.objects.filter(pk=membership.pk).update(is_admin=is_admin)
            changed = True

    if changed or removed or added:
        _broadcast_on_commit(room.id, desired.keys(), removed)

    return room
