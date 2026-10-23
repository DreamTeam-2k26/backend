"""Camada de domínio do chat: salas, participantes e mensagens.

Funções síncronas: as views as chamam direto e o consumer, via database_sync_to_async.
Toda função que age em nome de um usuário valida a participação dele na sala aqui dentro,
então REST e WebSocket compartilham exatamente as mesmas regras de autorização.
"""

import logging
from datetime import UTC, datetime
from pathlib import PurePath

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, DateTimeField, F, IntegerField, OuterRef, Subquery, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from uploader.helpers.files import get_content_type

from .models import Membership, Message, Room
from .models.message import MESSAGE_MAX
from .models.room import ROOM_NAME_MAX

logger = logging.getLogger('chat')

PAGE_SIZE = 30
NEVER_READ = datetime(1970, 1, 1, tzinfo=UTC)  # 'sem leitura' nas subqueries

NOT_A_MEMBER = 'Você não participa desta sala.'
ADMIN_ONLY = 'Apenas administradores podem fazer isso.'
MESSAGE_NOT_FOUND = 'Mensagem não encontrada.'


def _clean_name(name):
    """Valida e normaliza o nome da sala (1 a ROOM_NAME_MAX caracteres)."""
    if name is not None and not isinstance(name, str):
        raise ValidationError('Nome inválido.')

    name = (name or '').strip()

    if not name or len(name) > ROOM_NAME_MAX:
        raise ValidationError(f'O nome da sala deve ter de 1 a {ROOM_NAME_MAX} caracteres.')

    return name


# ---------------------------------------------------------------------------
# Participação e autorização
# ---------------------------------------------------------------------------


def get_membership(user, room):
    """Retorna a Membership do usuário ou levanta PermissionDenied se ele não participa da sala."""
    if not (user and user.is_authenticated):
        raise PermissionDenied(NOT_A_MEMBER)

    try:
        return Membership.objects.get(user=user, room=room)
    except Membership.DoesNotExist as error:
        raise PermissionDenied(NOT_A_MEMBER) from error


def get_room_for(user, room_id):
    """Sala pelo id, somente se o usuário participa dela (senão None, sem revelar se a sala existe)."""
    if not (user and user.is_authenticated):
        return None

    return Room.objects.filter(pk=room_id, memberships__user=user).first()


def member_ids_for(room_id):
    """Ids dos participantes (lista vazia se a sala foi apagada, ou seja, o último participante saiu)."""
    if room_id is None:
        return []

    return list(Membership.objects.filter(room_id=room_id).values_list('user_id', flat=True))


def member_ids(room):
    return member_ids_for(room.pk)


def _require_admin(actor, room):
    membership = get_membership(actor, room)

    if not membership.is_admin:
        raise PermissionDenied(ADMIN_ONLY)

    return membership


# ---------------------------------------------------------------------------
# Salas
# ---------------------------------------------------------------------------


def create_room(creator, name, members=(), image=None):
    """Cria uma sala; o criador entra como administrador e os demais como participantes."""
    name = _clean_name(name)
    others = {user.pk: user for user in members if user.pk != creator.pk and user.is_active}

    with transaction.atomic():
        room = Room.objects.create(name=name, image=image, created_by=creator)
        Membership.objects.create(user=creator, room=room, is_admin=True)
        Membership.objects.bulk_create([Membership(user=user, room=room) for user in others.values()])

    return room


def update_room(actor, room, name=None, image=None, *, clear_image=False):
    """Atualiza nome e/ou imagem da sala (somente administradores)."""
    _require_admin(actor, room)

    if name is not None:
        room.name = _clean_name(name)

    if image is not None:
        room.image = image
    elif clear_image:
        room.image = None

    room.save()

    return room


def add_members(actor, room, users):
    """Adiciona usuários à sala (somente administradores). Quem já participa é ignorado."""
    _require_admin(actor, room)
    existing = set(Membership.objects.filter(room=room).values_list('user_id', flat=True))
    new_users = {user.pk: user for user in users if user.pk not in existing and user.is_active}

    if not new_users:
        return []

    Membership.objects.bulk_create([Membership(user=user, room=room) for user in new_users.values()])

    return list(new_users.values())


def remove_member(actor, room, user):
    """Remove outro participante (somente administradores); para sair, use leave_room."""
    _require_admin(actor, room)

    if actor.pk == user.pk:
        raise ValidationError('Use a ação de sair para deixar a sala.')

    deleted, _ = Membership.objects.filter(user=user, room=room).delete()

    if not deleted:
        raise ValidationError('O usuário não participa da sala.')


def set_admin(actor, room, user, is_admin):
    """Promove ou rebaixa um participante (somente administradores)."""
    _require_admin(actor, room)

    with transaction.atomic():
        target = Membership.objects.select_for_update().filter(user=user, room=room).first()

        if target is None:
            raise ValidationError('O usuário não participa da sala.')

        if target.is_admin and not is_admin:
            admins = Membership.objects.filter(room=room, is_admin=True).count()

            if admins <= 1:
                raise ValidationError('A sala precisa ter pelo menos um administrador.')

        target.is_admin = is_admin
        target.save(update_fields=['is_admin'])


def leave_room(user, room):
    """Remove o usuário da sala; promove o participante mais antigo se não sobrar administrador
    e apaga a sala (com os arquivos das mensagens) se ela ficar vazia."""
    with transaction.atomic():
        get_membership(user, room).delete()
        remaining = Membership.objects.filter(room=room).order_by('joined_at', 'id')
        first = remaining.first()

        if first is None:
            _delete_room_files(room)
            room.delete()
        elif not remaining.filter(is_admin=True).exists():
            first.is_admin = True
            first.save(update_fields=['is_admin'])


def _delete_room_files(room):
    for message in room.messages.exclude(file='').exclude(file__isnull=True):
        _remove_file(message)


def _remove_file(message):
    """Apaga o arquivo do storage sem derrubar a operação principal se o storage falhar."""
    try:
        message.file.delete(save=False)
    except Exception:  # noqa: BLE001 - falha de storage não pode impedir a exclusão lógica
        logger.exception('Falha ao remover o arquivo da mensagem %s', message.pk)


def rooms_for(user, only=None):
    """Salas do usuário, da mais recente para a mais antiga, anotadas com
    .unread, .last_message, .member_count e .is_admin (número de queries constante).
    `only` restringe a uma sala (usado por room_summary)."""
    mine = Membership.objects.filter(room=OuterRef('pk'), user=user)
    last_read = mine.values('last_read_at')[:1]
    unread = (
        Message.objects.filter(room=OuterRef('pk'), is_deleted=False, created__gt=OuterRef('my_last_read'))
        .exclude(author=user)
        .order_by()
        .values('room')
        .annotate(total=Count('pk'))
        .values('total')
    )
    last_id = Message.objects.filter(room=OuterRef('pk')).order_by('-created', '-id').values('id')[:1]
    count = Membership.objects.filter(room=OuterRef('pk')).order_by().values('room').annotate(total=Count('pk'))

    rooms = list(
        Room.objects.filter(memberships__user=user, **({'pk': only.pk} if only is not None else {}))
        .select_related('image')
        .annotate(
            my_last_read=Coalesce(
                Subquery(last_read), Value(NEVER_READ, output_field=DateTimeField()), output_field=DateTimeField()
            ),
            unread=Coalesce(Subquery(unread, output_field=IntegerField()), 0),
            last_message_id=Subquery(last_id),
            member_count=Coalesce(Subquery(count.values('total'), output_field=IntegerField()), 0),
            is_admin=Coalesce(Subquery(mine.values('is_admin')[:1]), False),
        )
        .order_by(F('last_message_at').desc(nulls_last=True), '-id')
    )

    ids = [room.last_message_id for room in rooms if room.last_message_id]
    messages = Message.objects.select_related('author__profile_picture', 'reply_to__author').in_bulk(ids)

    for room in rooms:
        room.last_message = messages.get(room.last_message_id)

    return rooms


def room_summary(user, room):
    """O mesmo que rooms_for, para uma única sala (mesmas anotações)."""
    rooms = rooms_for(user, only=room)

    if not rooms:
        raise PermissionDenied(NOT_A_MEMBER)

    return rooms[0]


# ---------------------------------------------------------------------------
# Mensagens
# ---------------------------------------------------------------------------


def _clean_body(body):
    """Valida e normaliza o texto da mensagem (1 a MESSAGE_MAX caracteres)."""
    if body is not None and not isinstance(body, str):
        raise ValidationError('Mensagem inválida.')

    body = (body or '').strip()

    if not body or len(body) > MESSAGE_MAX:
        raise ValidationError(f'A mensagem deve ter de 1 a {MESSAGE_MAX} caracteres.')

    return body


def _resolve_reply(room, reply_to_id):
    if reply_to_id is None:
        return None

    reply_to = Message.objects.filter(pk=reply_to_id, room=room).first()

    if reply_to is None:
        raise ValidationError('A mensagem respondida não pertence a esta sala.')

    return reply_to


def send_message(user, room, body=None, file=None, reply_to_id=None):
    """Envia texto ou arquivo (exatamente um dos dois) para uma sala da qual o usuário participa."""
    get_membership(user, room)

    if (body is None) == (file is None):
        raise ValidationError('Envie texto ou arquivo, não ambos nem nenhum.')

    extra = {}

    if file is not None:
        limit = settings.CHAT_MAX_UPLOAD_MB * 1024 * 1024

        if not file.size:
            raise ValidationError('O arquivo está vazio.')

        if file.size > limit:
            raise ValidationError(f'O arquivo excede {settings.CHAT_MAX_UPLOAD_MB} MB.')

        extra = {
            'file_name': PurePath(file.name or 'arquivo').name[:255] or 'arquivo',
            'file_size': file.size,
            # Tipo real pelo conteúdo (libmagic), o mesmo mecanismo do uploader da plataforma.
            'file_content_type': get_content_type(file),
        }
    else:
        body = _clean_body(body)

    reply_to = _resolve_reply(room, reply_to_id)

    with transaction.atomic():
        message = Message.objects.create(room=room, author=user, body=body, file=file, reply_to=reply_to, **extra)
        Room.objects.filter(pk=room.pk).update(last_message_at=message.created)
        room.last_message_at = message.created
        Membership.objects.filter(user=user, room=room).update(last_read_at=message.created)

    return message


def _own_message(user, room, message_id):
    """Mensagem da sala escrita pelo usuário (que ainda precisa participar da sala)."""
    get_membership(user, room)
    message = Message.objects.filter(pk=message_id, room=room).first()

    if message is None:
        raise Message.DoesNotExist(MESSAGE_NOT_FOUND)

    if message.author_id != user.pk:
        raise PermissionDenied('Apenas o autor pode alterar a mensagem.')

    return message


def edit_message(user, room, message_id, body):
    """Edita o texto de uma mensagem própria (não apagada e sem arquivo)."""
    message = _own_message(user, room, message_id)

    if message.is_deleted:
        raise ValidationError('Mensagem apagada não pode ser editada.')

    if message.file:
        raise ValidationError('Só mensagens de texto podem ser editadas.')

    message.body = _clean_body(body)
    message.edited_at = timezone.now()
    message.save(update_fields=['body', 'edited_at'])

    return message


def delete_message(user, room, message_id):
    """Apaga (exclusão lógica) uma mensagem própria e remove o arquivo do storage."""
    message = _own_message(user, room, message_id)

    if message.is_deleted:
        return message

    if message.file:
        _remove_file(message)

    message.file = None
    message.file_name = ''
    message.file_size = None
    message.file_content_type = ''
    message.body = None
    message.is_deleted = True
    message.save(update_fields=['file', 'file_name', 'file_size', 'file_content_type', 'body', 'is_deleted'])

    return message


def get_message_for(user, message_id):
    """Mensagem pelo id somente se o usuário participa da sala dela (senão None)."""
    if not (user and user.is_authenticated):
        return None

    return Message.objects.filter(pk=message_id, room__memberships__user=user).select_related('room').first()


def read_by_all_ids(room, messages):
    """Ids das mensagens já lidas por todos os demais participantes da sala (1 query)."""
    reads = list(Membership.objects.filter(room=room).values_list('user_id', 'last_read_at'))
    result = set()

    for message in messages:
        others = [read for user_id, read in reads if user_id != message.author_id]

        if others and all(read is not None and read >= message.created for read in others):
            result.add(message.id)

    return result


def mark_read(user, room):
    """Marca a sala como lida; retorna os ids das mensagens de outros autores que
    passaram a estar lidas por todos."""
    membership = get_membership(user, room)
    previous = membership.last_read_at
    membership.last_read_at = timezone.now()
    membership.save(update_fields=['last_read_at'])

    queryset = Message.objects.filter(room=room, is_deleted=False).exclude(author=user)

    if previous:
        queryset = queryset.filter(created__gt=previous)

    candidates = list(queryset.filter(created__lte=membership.last_read_at))
    done = read_by_all_ids(room, candidates)

    return [message.id for message in candidates if message.id in done]


def message_page(room, before_id=None, size=PAGE_SIZE):
    """Até `size` mensagens mais recentes (anteriores a before_id), em ordem cronológica,
    e se existem mensagens ainda mais antigas."""
    queryset = Message.objects.filter(room=room)

    if before_id is not None:
        queryset = queryset.filter(pk__lt=before_id)

    page = list(
        queryset.select_related('author__profile_picture', 'reply_to__author').order_by('-id')[: size + 1],
    )
    has_more = len(page) > size
    page = page[:size]
    page.reverse()

    return page, has_more


def search_users(requester, term, exclude_room_id=None):
    """Usuários ativos cujo e-mail contém o termo (mínimo de 2 caracteres), exceto o próprio solicitante."""
    term = (term or '').strip()

    if len(term) < 2:  # noqa: PLR2004
        return get_user_model().objects.none()

    queryset = (
        get_user_model()
        .objects.filter(is_active=True, email__icontains=term)
        .exclude(pk=requester.pk)
        .select_related('profile_picture')
        .order_by('email')
    )

    # Só filtra por participantes de uma sala da qual o próprio solicitante participa.
    if exclude_room_id is not None and get_room_for(requester, exclude_room_id) is not None:
        queryset = queryset.exclude(chat_memberships__room_id=exclude_room_id)

    return queryset
