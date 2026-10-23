from django.db import transaction
from rest_framework.serializers import CharField, ModelSerializer, ValidationError

from core.models import Membership


class MembershipListSerializer(ModelSerializer):
    user_email = CharField(source='user.email', read_only=True)
    user_name = CharField(source='user.name', read_only=True)

    class Meta:
        model = Membership
        fields = ('id', 'project', 'user', 'user_email', 'user_name', 'role', 'joined_at')


class MembershipRetrieveSerializer(ModelSerializer):
    user_email = CharField(source='user.email', read_only=True)
    user_name = CharField(source='user.name', read_only=True)
    project_name = CharField(source='project.name', read_only=True)

    class Meta:
        model = Membership
        fields = ('id', 'project', 'project_name', 'user', 'user_email', 'user_name', 'role', 'joined_at')


class MembershipCreateUpdateSerializer(ModelSerializer):
    """Troca o papel de um participante. role = owner transfere a propriedade do projeto."""

    class Meta:
        model = Membership
        fields = ('id', 'project', 'user', 'role')
        read_only_fields = ('project', 'user')

    def validate_role(self, role):
        if self.instance and self.instance.role == Membership.Role.OWNER and role != Membership.Role.OWNER:
            raise ValidationError(
                'O dono não pode mudar o próprio papel. Transfira a propriedade para outro participante antes.'
            )
        return role

    @transaction.atomic
    def update(self, membership, validated_data):
        if validated_data.get('role') == Membership.Role.OWNER and membership.role != Membership.Role.OWNER:
            # Rebaixa o dono atual antes de promover o novo: a constraint permite só 1 owner por projeto.
            current_owner = Membership.objects.select_for_update().get(
                project=membership.project, role=Membership.Role.OWNER
            )
            current_owner.role = Membership.Role.ADMIN
            current_owner.save()
        return super().update(membership, validated_data)
