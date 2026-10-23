from django.db.models import Count, OuterRef, Prefetch, Q, Subquery
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from core.models import Column, Membership, Project
from core.permissions import ProjectRolePermission
from core.serializers import ProjectCreateUpdateSerializer, ProjectListSerializer, ProjectRetrieveSerializer


@extend_schema_view(
    list=extend_schema(summary='Projetos do usuário'),
    create=extend_schema(summary='Criar projeto (o criador vira dono)'),
    retrieve=extend_schema(summary='Detalhes do projeto'),
    update=extend_schema(summary='Editar projeto (dono ou administrador)'),
    partial_update=extend_schema(summary='Editar parte do projeto (dono ou administrador)'),
    destroy=extend_schema(summary='Excluir projeto (somente dono)'),
)
class ProjectViewSet(ModelViewSet):
    """Projetos do usuário autenticado.

    A autorização é por PARTICIPAÇÃO no projeto (Membership), não pelas permissões de modelo dos grupos;
    por isso usa IsAuthenticated + ProjectRolePermission no lugar do ModelPermissions padrão.
    Um projeto do qual o usuário não participa responde 404, igual a um projeto inexistente.
    """

    permission_classes = [IsAuthenticated, ProjectRolePermission]
    lookup_value_regex = r'\d+'

    def get_queryset(self):
        user = self.request.user
        role = Membership.objects.filter(project=OuterRef('pk'), user=user).values('role')[:1]
        queryset = Project.objects.filter(memberships__user=user).annotate(role=Subquery(role)).order_by('-created_at')
        if self.action == 'list':
            # Participantes do card (id e foto) em 1 consulta para todos os projetos da página, não 1 por projeto.
            participants = Membership.objects.select_related('user__profile_picture').order_by('joined_at', 'id')
            queryset = queryset.prefetch_related(Prefetch('memberships', queryset=participants)).annotate(
                # Para o progress: concluída = tarefa em coluna de categoria done (inclui colunas personalizadas).
                total_tasks=Count('tasks', distinct=True),
                done_tasks=Count('tasks', filter=Q(tasks__column__category=Column.Category.DONE), distinct=True),
            )
        return queryset

    def get_serializer_class(self):
        if self.action == 'list':
            return ProjectListSerializer
        if self.action in {'create', 'update', 'partial_update'}:
            return ProjectCreateUpdateSerializer
        return ProjectRetrieveSerializer

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)
