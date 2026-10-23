from django.db import transaction
from rest_framework.serializers import CharField, IntegerField, ModelSerializer, SerializerMethodField

from core.models import Column, Membership, Project

# `role` = papel do usuário logado no projeto, anotado no get_queryset da ProjectViewSet.


class ProjectMembershipSerializer(ModelSerializer):
    """Participante no card do projeto: id do usuário e URL da foto (null se não tiver)."""

    id = IntegerField(source='user_id', read_only=True)
    profile_picture = SerializerMethodField()

    class Meta:
        model = Membership
        fields = ('id', 'profile_picture')

    def get_profile_picture(self, membership) -> str | None:
        image = membership.user.profile_picture
        return image.url if image else None


class ProjectListSerializer(ModelSerializer):
    # memberships vem pré-carregado pelo get_queryset da ProjectViewSet (sem consulta extra por projeto).
    role = CharField(read_only=True)
    total_memberships = SerializerMethodField()
    memberships = ProjectMembershipSerializer(many=True, read_only=True)

    class Meta:
        model = Project
        fields = ('id', 'name', 'description', 'total_memberships', 'memberships', 'role', 'updated_at')

    def get_total_memberships(self, project) -> int:
        return len(project.memberships.all())


class ProjectRetrieveSerializer(ModelSerializer):
    role = CharField(read_only=True)

    class Meta:
        model = Project
        fields = ('id', 'name', 'description', 'created_by', 'created_at', 'updated_at', 'role')


class ProjectCreateUpdateSerializer(ModelSerializer):
    role = CharField(read_only=True)

    class Meta:
        model = Project
        fields = ('id', 'name', 'description', 'role')

    @transaction.atomic
    def create(self, validated_data):
        # created_by vem do perform_create da viewset (usuário autenticado), nunca do corpo da requisição.
        project = Project.objects.create(**validated_data)
        Membership.objects.create(project=project, user=project.created_by, role=Membership.Role.OWNER)
        Column.create_defaults(project)
        project.role = Membership.Role.OWNER
        return project
