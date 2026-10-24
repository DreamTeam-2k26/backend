from datetime import timedelta

from django.utils import timezone

from calls import services
from calls.models import Call
from chat.tests.helpers import access_token
from core.tests.helpers import client_for, make_project, make_user

__all__ = ['access_token', 'client_for', 'make_project', 'make_user', 'future', 'live_call', 'setup_team']


def future(hours=2):
    return timezone.now() + timedelta(hours=hours)


def setup_team():
    """Projeto com dono, administrador e membro, mais um usuário de fora."""
    owner, admin, member, outsider = (make_user(name) for name in ('dona', 'admin', 'membro', 'fora'))
    project = make_project(owner, admins=[admin], members=[member])

    return project, owner, admin, member, outsider


def live_call(project, user, title='Reunião'):
    return services.start_now(project, user, title)


def scheduled_call(project, user, title='Planejamento', hours=2):
    return services.schedule(project, user, title, future(hours))


__all__ += ['scheduled_call', 'Call']
