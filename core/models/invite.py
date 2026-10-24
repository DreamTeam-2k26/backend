import secrets

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from .project import Membership, Project


def generate_invite_token():
    """Token de link: 256 bits aleatórios (secrets), seguro para URL e não adivinhável."""
    return secrets.token_urlsafe(32)


class Invite(models.Model):
    """Convite para um projeto. Só o aceite (direct) ou o resgate (link) cria a Membership."""

    class Kind(models.TextChoices):
        DIRECT = 'direct', 'Usuário cadastrado'
        LINK = 'link', 'Link compartilhável'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pendente'
        ACCEPTED = 'accepted', 'Aceito'
        DECLINED = 'declined', 'Recusado'
        REVOKED = 'revoked', 'Revogado'
        EXPIRED = 'expired', 'Expirado'

    # Convite nunca cria dono: a propriedade só muda por transferência (MembershipCreateUpdateSerializer).
    ROLE_CHOICES = [(Membership.Role.ADMIN, 'Administrador'), (Membership.Role.MEMBER, 'Membro')]

    project = models.ForeignKey(Project, related_name='invites', on_delete=models.CASCADE)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    invited_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='received_invites',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='sent_invites',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    token = models.CharField(max_length=64, null=True, blank=True)
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default=Membership.Role.MEMBER)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Convite'
        verbose_name_plural = 'Convites'
        constraints = [
            models.UniqueConstraint(fields=['token'], name='uq_invite_token'),
            # link sempre tem token e nunca destinatário; direct sempre tem destinatário e nunca token.
            models.CheckConstraint(
                condition=(Q(kind='link', token__isnull=False, invited_user__isnull=True))
                | Q(kind='direct', token__isnull=True, invited_user__isnull=False),
                name='ck_invite_kind_fields',
            ),
            models.UniqueConstraint(
                fields=['project', 'invited_user'],
                condition=Q(kind='direct', status='pending'),
                name='uq_invite_pending_direct',
            ),
        ]

    def __str__(self):
        target = self.invited_user if self.kind == self.Kind.DIRECT else 'link'
        return f'Convite para {self.project} ({target}, {self.status})'

    @property
    def is_expired(self):
        return self.expires_at is not None and self.expires_at <= timezone.now()
