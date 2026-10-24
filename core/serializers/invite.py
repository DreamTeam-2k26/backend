from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.serializers import (
    CharField,
    EmailField,
    ModelSerializer,
    PrimaryKeyRelatedField,
    Serializer,
    ValidationError,
)

from core.models import Invite, Membership, Project, User
from core.models.invite import generate_invite_token

MANAGER_ROLES = (Membership.Role.OWNER, Membership.Role.ADMIN)


def manager_role(user, project):
    """Papel de quem convida. Fora do projeto: 404; sem papel de dono/admin: 403."""
    membership = Membership.objects.filter(project=project, user=user).first()
    if membership is None:
        raise NotFound('Projeto não encontrado.')
    if membership.role not in MANAGER_ROLES:
        raise PermissionDenied('Só o dono ou um administrador pode convidar participantes.')
    return membership.role


def check_invite_usable(invite):
    """Convite só pode ser usado enquanto pendente e dentro da validade."""
    if invite.status != Invite.Status.PENDING:
        raise ValidationError(f'Este convite não está mais pendente ({invite.get_status_display().lower()}).')
    if invite.is_expired:
        Invite.objects.filter(pk=invite.pk).update(status=Invite.Status.EXPIRED)
        raise ValidationError('Este convite expirou.')


def check_not_participant(user, project):
    if Membership.objects.filter(project=project, user=user).exists():
        raise ValidationError('Você já participa deste projeto.')


class InviteListSerializer(ModelSerializer):
    invited_user_name = CharField(source='invited_user.name', read_only=True, default=None)

    class Meta:
        model = Invite
        fields = (
            'id',
            'project',
            'kind',
            'invited_user',
            'invited_user_name',
            'role',
            'status',
            'token',
            'expires_at',
            'created_at',
        )


class InviteRetrieveSerializer(ModelSerializer):
    project_name = CharField(source='project.name', read_only=True)
    invited_user_name = CharField(source='invited_user.name', read_only=True, default=None)
    invited_by_name = CharField(source='invited_by.name', read_only=True, default=None)

    class Meta:
        model = Invite
        fields = (
            'id',
            'project',
            'project_name',
            'kind',
            'invited_user',
            'invited_user_name',
            'invited_by',
            'invited_by_name',
            'role',
            'status',
            'token',
            'expires_at',
            'created_at',
            'responded_at',
        )


class InviteReceivedSerializer(ModelSerializer):
    """Convite direto visto pelo destinatário (nunca mostra token)."""

    project_name = CharField(source='project.name', read_only=True)
    invited_by_name = CharField(source='invited_by.name', read_only=True, default=None)

    class Meta:
        model = Invite
        fields = ('id', 'project', 'project_name', 'invited_by_name', 'role', 'expires_at', 'created_at')


class InviteCreateSerializer(ModelSerializer):
    """Cria convite. invited_by vem do perform_create; token e status são do servidor."""

    invited_user = PrimaryKeyRelatedField(queryset=User.objects.filter(is_active=True), required=False, allow_null=True)

    class Meta:
        model = Invite
        fields = ('id', 'project', 'kind', 'invited_user', 'role', 'expires_at', 'status', 'token')
        read_only_fields = ('status', 'token')
        # O DRF geraria um UniqueTogetherValidator da constraint de convite pendente que torna invited_user
        # obrigatório até em link. O duplicado é checado em validate() (mensagem em português) e no banco.
        validators = []

    def validate_project(self, project):
        self.actor_role = manager_role(self.context['request'].user, project)
        return project

    def validate_expires_at(self, expires_at):
        if expires_at is not None and expires_at <= timezone.now():
            raise ValidationError('A data de expiração deve estar no futuro.')
        return expires_at

    def validate(self, attrs):
        project, invited_user = attrs['project'], attrs.get('invited_user')

        if attrs.get('role') == Membership.Role.ADMIN and self.actor_role != Membership.Role.OWNER:
            raise PermissionDenied('Só o dono pode convidar como administrador.')

        if attrs['kind'] == Invite.Kind.LINK:
            if invited_user is not None:
                raise ValidationError({'invited_user': 'Convite por link não tem destinatário.'})
            return attrs

        if invited_user is None:
            raise ValidationError({'invited_user': 'Informe o usuário convidado.'})
        if Membership.objects.filter(project=project, user=invited_user).exists():
            raise ValidationError({'invited_user': 'Este usuário já participa do projeto.'})
        pending = Invite.objects.filter(
            project=project, invited_user=invited_user, kind=Invite.Kind.DIRECT, status=Invite.Status.PENDING
        )
        if pending.exists():
            raise ValidationError({'invited_user': 'Já existe um convite pendente para este usuário.'})
        return attrs

    def create(self, validated_data):
        if validated_data['kind'] == Invite.Kind.LINK:
            validated_data['token'] = generate_invite_token()
        return super().create(validated_data)


class InviteAcceptSerializer(Serializer):
    """Aceite de convite direto. Espera context['invite'] (já autorizado para o destinatário)."""

    def validate(self, attrs):
        invite = self.context['invite']
        if invite.kind != Invite.Kind.DIRECT:
            raise ValidationError('Convites por link são resgatados pelo token.')
        check_invite_usable(invite)
        check_not_participant(invite.invited_user, invite.project)
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        invite = Invite.objects.select_for_update().get(pk=self.context['invite'].pk)
        check_invite_usable(invite)
        membership = Membership.objects.create(project=invite.project, user=invite.invited_user, role=invite.role)
        invite.status = Invite.Status.ACCEPTED
        invite.responded_at = timezone.now()
        invite.save(update_fields=['status', 'responded_at'])
        return membership


class InviteDeclineSerializer(Serializer):
    """Recusa de convite direto. Espera context['invite']."""

    def validate(self, attrs):
        check_invite_usable(self.context['invite'])
        return attrs

    def create(self, validated_data):
        invite = self.context['invite']
        invite.status = Invite.Status.DECLINED
        invite.responded_at = timezone.now()
        invite.save(update_fields=['status', 'responded_at'])
        return invite


class InviteRevokeSerializer(Serializer):
    """Revogação por dono/admin. Espera context['invite']."""

    def validate(self, attrs):
        invite = self.context['invite']
        if invite.status != Invite.Status.PENDING:
            raise ValidationError('Só convites pendentes podem ser revogados.')
        return attrs

    def create(self, validated_data):
        invite = self.context['invite']
        invite.status = Invite.Status.REVOKED
        invite.save(update_fields=['status'])
        return invite


class InviteRedeemSerializer(Serializer):
    """Resgate de link: cria a Membership com o papel do convite. O link continua pendente (reutilizável)."""

    token = CharField(max_length=64)

    def validate_token(self, token):
        invite = Invite.objects.filter(kind=Invite.Kind.LINK, token=token).select_related('project').first()
        if invite is None:
            raise ValidationError('Convite inválido.')
        check_invite_usable(invite)
        check_not_participant(self.context['request'].user, invite.project)
        self.invite = invite
        return token

    def create(self, validated_data):
        try:
            with transaction.atomic():
                return Membership.objects.create(
                    project=self.invite.project, user=self.context['request'].user, role=self.invite.role
                )
        except IntegrityError as error:
            # Dois resgates simultâneos do mesmo usuário: o UniqueConstraint barra o segundo.
            raise ValidationError({'token': 'Você já participa deste projeto.'}) from error


class InviteFindUserSerializer(Serializer):
    """Parâmetros da busca de usuário para convidar (e-mail exato)."""

    project = PrimaryKeyRelatedField(queryset=Project.objects.all())
    email = EmailField()

    def validate_project(self, project):
        manager_role(self.context['request'].user, project)
        return project


class InviteUserSerializer(ModelSerializer):
    """Identificação mínima do usuário encontrado (sem e-mail nem permissões)."""

    class Meta:
        model = User
        fields = ('id', 'name')
