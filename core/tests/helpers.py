from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Column, Membership, Project

User = get_user_model()

PROJECTS = '/api/projects/'


def make_user(name, **extra):
    return User.objects.create_user(email=f'{name}@example.com', password='senha-forte-123', **extra)


def make_project(owner, name='Feira de Ciências', *, admins=(), members=()):
    project = Project.objects.create(name=name, created_by=owner)
    Membership.objects.create(project=project, user=owner, role=Membership.Role.OWNER)
    Column.create_defaults(project)

    for user in admins:
        Membership.objects.create(project=project, user=user, role=Membership.Role.ADMIN)

    for user in members:
        Membership.objects.create(project=project, user=user, role=Membership.Role.MEMBER)

    return project


def project_url(project):
    return f'{PROJECTS}{project.pk}/'


def client_for(user):
    client = APIClient()
    client.force_authenticate(user)

    return client


def jwt_client(email, password='senha-forte-123'):
    """Login de verdade (POST /api/token/) e cliente com o access no Authorization, como o frontend faz."""
    tokens = APIClient().post('/api/token/', {'email': email, 'password': password})
    assert tokens.status_code == status.HTTP_200_OK, tokens.data  # noqa: S101 - falha de setup do teste
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Bearer {tokens.data["access"]}')

    return client
