from django.db import transaction
from rest_framework.serializers import CharField, IntegerField, ModelSerializer, Serializer, SerializerMethodField

from core.models import Column, Membership, Project

# `role` = papel do usuário logado no projeto, anotado no get_queryset da ProjectViewSet.


def progress_percent(done, total):
    """Progresso do projeto: tarefas em colunas done / total * 100, arredondado; 0 quando não há tarefas."""
    if not total:
        return 0
    return round(done * 100 / total)


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
    # memberships, total_tasks e done_tasks vêm do get_queryset da ProjectViewSet (sem consulta extra por projeto).
    role = CharField(read_only=True)
    total_memberships = SerializerMethodField()
    memberships = ProjectMembershipSerializer(many=True, read_only=True)
    progress = SerializerMethodField()

    class Meta:
        model = Project
        fields = ('id', 'name', 'description', 'total_memberships', 'memberships', 'progress', 'role', 'updated_at')

    def get_total_memberships(self, project) -> int:
        return len(project.memberships.all())

    def get_progress(self, project) -> int:
        """Percentual (0 a 100) de tarefas concluídas; projeto sem tarefas é 0."""
        return progress_percent(project.done_tasks, project.total_tasks)


class ProjectDashboardSerializer(Serializer):
    """Visão Geral do projeto. Recebe o dicionário do aggregate feito na action dashboard da ProjectViewSet."""

    total_tasks = IntegerField()
    done_tasks = IntegerField(help_text='Tarefas em colunas de categoria done.')
    pending_tasks = SerializerMethodField(help_text='Tarefas fora de colunas done.')
    todo_tasks = IntegerField()
    in_progress_tasks = IntegerField()
    overdue_tasks = IntegerField(help_text='Prazo anterior a hoje e fora de colunas done.')
    progress = SerializerMethodField(help_text='Percentual (0 a 100) de tarefas concluídas; 0 sem tarefas.')

    def get_pending_tasks(self, stats) -> int:
        return stats['total_tasks'] - stats['done_tasks']

    def get_progress(self, stats) -> int:
        return progress_percent(stats['done_tasks'], stats['total_tasks'])


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
