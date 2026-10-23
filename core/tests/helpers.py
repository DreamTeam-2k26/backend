from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from core.models import Membership, Project

User = get_user_model()

PROJECTS = '/api/projects/'


def make_user(name, **extra):
    return User.objects.create_user(email=f'{name}@example.com', password='senha-forte-123', **extra)


def make_project(owner, name='Feira de Ciências', *, admins=(), members=()):
    project = Project.objects.create(name=name, created_by=owner)
    Membership.objects.create(project=project, user=owner, role=Membership.Role.OWNER)

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
