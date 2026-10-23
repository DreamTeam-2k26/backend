from django.db.models import Count, OuterRef, Subquery
from django.db.models.functions import Lower
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from core.models import Folder, Membership
from core.permissions import FolderPermission
from core.serializers import FolderCreateUpdateSerializer, FolderListSerializer, FolderRetrieveSerializer


@extend_schema_view(
    list=extend_schema(summary='Conteúdo de pastas: raiz com ?project=<id>&parent__isnull=true, ou ?parent=<id>'),
    create=extend_schema(summary='Criar pasta (qualquer participante; sem parent fica na raiz)'),
    retrieve=extend_schema(summary='Detalhes da pasta com o caminho (breadcrumb)'),
    update=extend_schema(summary='Renomear/mover pasta (qualquer participante)'),
    partial_update=extend_schema(summary='Renomear (name) ou mover (parent; null = raiz) a pasta'),
    destroy=extend_schema(summary='Excluir pasta e todas as subpastas (qualquer participante)'),
)
class FolderViewSet(ModelViewSet):
    """Pastas de arquivos dos projetos do usuário autenticado.

    Qualquer participante opera as pastas (FolderPermission). Pasta de projeto alheio responde 404.
    """

    queryset = Folder.objects.none()  # só para o schema/filtros descobrirem o model; o real é get_queryset
    permission_classes = [IsAuthenticated, FolderPermission]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = {'project': ['exact'], 'parent': ['exact', 'isnull']}
    lookup_value_regex = r'\d+'

    def get_queryset(self):
        user = self.request.user
        my_projects = Membership.objects.filter(user=user).values('project')
        my_role = Membership.objects.filter(project=OuterRef('project'), user=user).values('role')[:1]
        return (
            Folder.objects
            .filter(project__in=my_projects)
            .annotate(my_role=Subquery(my_role), children_count=Count('children'))
            .select_related('created_by')
            .order_by(Lower('name'), 'id')
        )

    def get_serializer_class(self):
        if self.action == 'list':
            return FolderListSerializer
        if self.action in {'create', 'update', 'partial_update'}:
            return FolderCreateUpdateSerializer
        return FolderRetrieveSerializer

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)
