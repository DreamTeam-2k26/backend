from django.conf import settings
from django.db import models
from django.db.models import Q

PROJECT_NAME_MAX = 100


class Project(models.Model):
    """Espaço de trabalho independente. Quem manda é a participação (Membership), não o created_by."""

    name = models.CharField(max_length=PROJECT_NAME_MAX)
    description = models.TextField(blank=True, default='')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='created_projects',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        through='Membership',
        related_name='projects',
        blank=True,
    )

    class Meta:
        verbose_name = 'Projeto'
        verbose_name_plural = 'Projetos'

    def __str__(self):
        return self.name


class Membership(models.Model):
    """Participação de um usuário em um projeto: é a única fonte de autorização do projeto."""

    class Role(models.TextChoices):
        OWNER = 'owner', 'Dono'
        ADMIN = 'admin', 'Administrador'
        MEMBER = 'member', 'Membro'

    project = models.ForeignKey(Project, related_name='memberships', on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='project_memberships', on_delete=models.CASCADE)
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.MEMBER)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Participação'
        verbose_name_plural = 'Participações'
        constraints = [
            models.UniqueConstraint(fields=['project', 'user'], name='uq_membership_project_user'),
            # No máximo 1 dono por projeto; "pelo menos 1" é garantido por ProjectCreateUpdateSerializer.create.
            models.UniqueConstraint(fields=['project'], condition=Q(role='owner'), name='uq_membership_project_owner'),
        ]

    def __str__(self):
        return f'{self.user} em {self.project} ({self.role})'
