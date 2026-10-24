"""Operações do chat = regra de negócio (services) + difusão em tempo real (realtime).

REST (views) e WebSocket (consumer) chamam estas mesmas funções, então os dois caminhos têm
exatamente a mesma validação, a mesma autorização e emitem os mesmos eventos.
Funções síncronas.
"""

from . import realtime, services

# ---- mensagens ----


def send_message(user, room, *, body=None, file=None, reply_to_id=None):
    message = services.send_message(user, room, body=body, file=file, reply_to_id=reply_to_id)
    realtime.broadcast_message_new(message)

    return message


def edit_message(user, room, message_id, body):
    message = services.edit_message(user, room, message_id, body)
    realtime.broadcast_message_update(message)

    return message


def delete_message(user, room, message_id):
    message = services.delete_message(user, room, message_id)
    realtime.broadcast_message_update(message)

    return message


def notify_typing(user, room):
    services.get_membership(user, room)
    realtime.broadcast_typing(user, room)


def mark_read(user, room):
    message_ids = services.mark_read(user, room)

    if message_ids:
        realtime.broadcast_read(room, message_ids)

    # Atualiza o contador (zerado) em todas as abas do leitor, mesmo sem ids novos.
    realtime.broadcast_room_changed(room.id, [user.pk])


# ---- salas ----


def create_room(creator, name, members=(), image=None):
    room = services.create_room(creator, name, members, image=image)
    realtime.broadcast_room_changed(room.id, services.member_ids(room))

    return room


def update_room(actor, room, name=None, image=None, *, clear_image=False):
    room = services.update_room(actor, room, name=name, image=image, clear_image=clear_image)
    realtime.broadcast_room_changed(room.id, services.member_ids(room))

    return room


def add_members(actor, room, users):
    added = services.add_members(actor, room, users)
    realtime.broadcast_room_changed(room.id, services.member_ids(room))

    return added


def remove_member(actor, room, user):
    services.remove_member(actor, room, user)
    realtime.broadcast_room_changed(room.id, [*services.member_ids(room), user.pk], removed_user_ids=[user.pk])


def set_admin(actor, room, user, is_admin):
    services.set_admin(actor, room, user, is_admin)
    realtime.broadcast_room_changed(room.id, services.member_ids(room))


def leave_room(user, room):
    room_id = room.pk
    services.leave_room(user, room)  # pode apagar a sala; por isso o id foi guardado antes
    realtime.broadcast_room_changed(room_id, [*services.member_ids_for(room_id), user.pk], removed_user_ids=[user.pk])
