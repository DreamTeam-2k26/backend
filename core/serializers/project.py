from django.db import transaction
from rest_framework.serializers import CharField, ModelSerializer

from core.models import Column, Membership, Project

# `role` = papel do usuário logado no projeto, anotado no get_queryset da ProjectViewSet.


class ProjectListSerializer(ModelSerializer):
    role = CharField(read_only=True)

    class Meta:
        model = Project
        fields = ('id', 'name', 'description', 'role', 'updated_at')


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
