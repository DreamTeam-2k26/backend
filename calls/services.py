"""Regras das chamadas. Views e consumers só orquestram; toda validação de negócio fica aqui."""

from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone

from core.models import Membership

from . import conf
from .models import TITLE_MAX, Call, Participant

DEFAULT_TITLE = 'Reunião do projeto'
# Chamada "em andamento" que ninguém chegou a abrir (ou que ficou vazia sem encerrar) é encerrada depois disso.
EMPTY_LIVE_GRACE = timedelta(minutes=10)
SCHEDULE_TOLERANCE = timedelta(minutes=1)


def role_in(user, project_id):
    return Membership.objects.filter(project_id=project_id, user=user).values_list('role', flat=True).first()


def is_manager(user, project_id):
    return role_in(user, project_id) in {Membership.Role.OWNER, Membership.Role.ADMIN}


def is_member(user, project_id):
    return Membership.objects.filter(project_id=project_id, user=user).exists()


def member_ids(project_id):
    return list(Membership.objects.filter(project_id=project_id).values_list('user_id', flat=True))


def require_manager(user, project_id):
    if not is_member(user, project_id):
        raise PermissionDenied('Projeto não encontrado.')

    if not is_manager(user, project_id):
        raise PermissionDenied('Só o dono ou um administrador pode fazer isso.')


def _clean_title(title):
    title = (title or '').strip()

    if len(title) > TITLE_MAX:
        raise ValidationError({'title': 'Use no máximo 150 caracteres.'})

    return title or DEFAULT_TITLE


def _go_live(call, now):
    call.status = Call.Status.LIVE
    call.started_at = now
    try:
        with transaction.atomic():
            call.save()
    except IntegrityError as error:
        raise ValidationError('Já existe uma chamada em andamento neste projeto.') from error

    return call


def start_now(project, user, title=''):
    """Cria e inicia uma chamada na hora (dono/admin)."""
    require_manager(user, project.pk)
    end_stale_calls(project.pk)

    return _go_live(Call(project=project, title=_clean_title(title), created_by=user), timezone.now())


def schedule(project, user, title, scheduled_for, description=''):
    """Agenda uma chamada futura (dono/admin)."""
    require_manager(user, project.pk)

    if scheduled_for is None or scheduled_for < timezone.now() - SCHEDULE_TOLERANCE:
        raise ValidationError({'scheduled_for': 'Escolha uma data e hora no futuro.'})

    return Call.objects.create(
        project=project,
        title=_clean_title(title),
        description=(description or '').strip(),
        scheduled_for=scheduled_for,
        created_by=user,
    )


def update_scheduled(call, user, **fields):
    require_manager(user, call.project_id)

    if call.status != Call.Status.SCHEDULED:
        raise ValidationError('Só chamadas agendadas podem ser editadas.')

    if 'title' in fields:
        call.title = _clean_title(fields['title'])

    if 'description' in fields:
        call.description = (fields['description'] or '').strip()

    if 'scheduled_for' in fields:
        when = fields['scheduled_for']

        if when is None or when < timezone.now() - SCHEDULE_TOLERANCE:
            raise ValidationError({'scheduled_for': 'Escolha uma data e hora no futuro.'})

        call.scheduled_for = when

    call.save()

    return call


def start_scheduled(call, user):
    """Inicia uma chamada agendada (dono/admin), a qualquer momento."""
    require_manager(user, call.project_id)

    if call.status != Call.Status.SCHEDULED:
        raise ValidationError('Esta chamada não está mais agendada.')

    end_stale_calls(call.project_id)

    return _go_live(call, timezone.now())


def end_call(call, user=None):
    """Encerra a chamada para todos. `user=None` é uso interno (ficou vazia)."""
    if user is not None:
        require_manager(user, call.project_id)

    if call.status != Call.Status.LIVE:
        raise ValidationError('Esta chamada não está em andamento.')

    return _finish(call)


def _finish(call):
    now = timezone.now()
    call.status = Call.Status.ENDED
    call.ended_at = now
    call.save(update_fields=['status', 'ended_at'])
    Participant.objects.filter(call=call, left_at__isnull=True).update(left_at=now)

    return call


def cancel(call, user):
    """Cancela (apaga) uma chamada agendada ou remove uma encerrada do histórico."""
    require_manager(user, call.project_id)

    if call.status == Call.Status.LIVE:
        raise ValidationError('Encerre a chamada antes de removê-la.')

    call.delete()


def end_stale_calls(project_id=None):
    """Encerra chamadas "em andamento" sem ninguém dentro há mais que a tolerância. Devolve as encerradas."""
    limit = timezone.now() - EMPTY_LIVE_GRACE
    someone_inside = Participant.objects.filter(call=OuterRef('pk'), left_at__isnull=True)
    stale = Call.objects.filter(status=Call.Status.LIVE, started_at__lt=limit).exclude(Exists(someone_inside))

    if project_id is not None:
        stale = stale.filter(project_id=project_id)

    ended = list(stale)

    for call in ended:
        _finish(call)

    return ended


def active_participants(call):
    return list(Participant.objects.filter(call=call, left_at__isnull=True).select_related('user'))


def active_participants_for(call_id):
    return list(Participant.objects.filter(call_id=call_id, left_at__isnull=True).select_related('user'))


def participant_with_user(participant_id):
    return Participant.objects.select_related('user').get(pk=participant_id)


def join(call_id, user, channel_name, state=None):
    """Entra na chamada em andamento. Devolve (participante, canal_substituído ou None).

    Entrar de outra aba assume o lugar da anterior (o canal antigo é avisado e se desconecta).
    """
    with transaction.atomic():
        call = Call.objects.select_for_update().filter(pk=call_id).first()

        if call is None or not is_member(user, call.project_id):
            raise PermissionDenied('Chamada não encontrada.')

        if call.status != Call.Status.LIVE:
            raise ValidationError('Esta chamada não está em andamento.')

        existing = Participant.objects.select_for_update().filter(call=call, user=user).first()
        active = Participant.objects.filter(call=call, left_at__isnull=True)

        if (existing is None or existing.left_at is not None) and active.count() >= conf.max_participants():
            raise ValidationError(f'A chamada está cheia (máximo de {conf.max_participants()} pessoas).')

        replaced = None
        now = timezone.now()

        if existing is None:
            participant = Participant.objects.create(
                call=call, user=user, channel_name=channel_name, state=state or {}, joined_at=now
            )
        else:
            if existing.left_at is None and existing.channel_name != channel_name:
                replaced = existing.channel_name

            existing.channel_name = channel_name
            existing.state = state or {}
            existing.joined_at = now
            existing.left_at = None
            existing.save()
            participant = existing

    return participant, replaced


def leave(call_id, user, channel_name):
    """Sai da chamada. Devolve (saiu, encerrou): só a conexão atual do usuário conta; vazia, a chamada acaba."""
    with transaction.atomic():
        call = Call.objects.select_for_update().filter(pk=call_id).first()
        participant = Participant.objects.filter(call_id=call_id, user=user, left_at__isnull=True).first()

        if call is None or participant is None or participant.channel_name != channel_name:
            return False, False

        participant.left_at = timezone.now()
        participant.save(update_fields=['left_at'])
        ended = False

        if call.status == Call.Status.LIVE and not active_participants(call):
            _finish(call)
            ended = True

    return True, ended


def set_state(call_id, user, channel_name, state):
    Participant.objects.filter(call_id=call_id, user=user, left_at__isnull=True, channel_name=channel_name).update(
        state=state
    )


def sanitize_state(raw):
    """Estado informado pelo cliente: só campos conhecidos e com tipos simples (nada é confiável)."""
    raw = raw if isinstance(raw, dict) else {}
    state = {key: bool(raw.get(key)) for key in ('mic', 'camera', 'screen', 'hand')}
    stream = raw.get('screen_stream')
    state['screen_stream'] = stream[:80] if isinstance(stream, str) and state['screen'] else ''

    return state


def live_for_user(user):
    """Chamadas em andamento dos projetos do usuário."""
    ids = Membership.objects.filter(user=user).values('project')
    end_stale_calls()

    return Call.objects.filter(project__in=ids, status=Call.Status.LIVE)
