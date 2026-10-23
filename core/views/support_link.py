from django.db.models import OuterRef, Subquery
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from core.models import Membership, SupportLink
from core.permissions import SupportLinkPermission
from core.serializers import (
    SupportLinkCreateUpdateSerializer,
    SupportLinkListSerializer,
    SupportLinkRetrieveSerializer,
)


@extend_schema_view(
    list=extend_schema(
        summary='Links de apoio (filtre com ?project=<id>, ?task=<id> ou ?task__isnull=true para os gerais)'
    ),
    create=extend_schema(summary='Cadastrar link (qualquer participante; autor vem do token)'),
    retrieve=extend_schema(summary='Detalhes do link'),
    update=extend_schema(summary='Editar link (qualquer participante)'),
    partial_update=extend_schema(summary='Editar link ou (des)vincular tarefa (qualquer participante)'),
    destroy=extend_schema(summary='Excluir link (qualquer participante)'),
)
class SupportLinkViewSet(ModelViewSet):
    """Links externos de apoio dos projetos do usuário autenticado (não substituem os arquivos do projeto).

    Qualquer participante opera os links (SupportLinkPermission). Link de projeto alheio responde 404.
    """

    queryset = SupportLink.objects.none()  # só para o schema/filtros descobrirem o model; o real é get_queryset
    permission_classes = [IsAuthenticated, SupportLinkPermission]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = {'project': ['exact'], 'task': ['exact', 'isnull']}
    lookup_value_regex = r'\d+'

    def get_queryset(self):
        user = self.request.user
        my_projects = Membership.objects.filter(user=user).values('project')
        my_role = Membership.objects.filter(project=OuterRef('project'), user=user).values('role')[:1]
        return (
            SupportLink.objects
            .filter(project__in=my_projects)
            .annotate(my_role=Subquery(my_role))
            .select_related('task', 'created_by')
            .order_by('-created_at', '-id')
        )

    def get_serializer_class(self):
        if self.action == 'list':
            return SupportLinkListSerializer
        if self.action in {'create', 'update', 'partial_update'}:
            return SupportLinkCreateUpdateSerializer
        return SupportLinkRetrieveSerializer

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)
