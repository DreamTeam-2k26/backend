from django.db import transaction
from django.db.models import OuterRef, Subquery
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.exceptions import ValidationError
from rest_framework.mixins import DestroyModelMixin, ListModelMixin, RetrieveModelMixin, UpdateModelMixin
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import GenericViewSet

from core.models import Membership, Task
from core.permissions import MembershipRolePermission
from core.serializers import MembershipCreateUpdateSerializer, MembershipListSerializer, MembershipRetrieveSerializer


@extend_schema_view(
    list=extend_schema(summary='Participantes dos projetos do usuário (filtre com ?project=<id>)'),
    retrieve=extend_schema(summary='Detalhes de uma participação'),
    update=extend_schema(summary='Alterar papel (somente dono; role=owner transfere a propriedade)'),
    partial_update=extend_schema(summary='Alterar papel (somente dono; role=owner transfere a propriedade)'),
    destroy=extend_schema(summary='Remover participante (dono remove admin/membro; admin remove membro)'),
)
class MembershipViewSet(ListModelMixin, RetrieveModelMixin, UpdateModelMixin, DestroyModelMixin, GenericViewSet):
    """Participações nos projetos do usuário autenticado.

    Sem criação direta: alguém entra no projeto aceitando um convite.
    Autorização pelo papel do usuário logado no projeto da participação (MembershipRolePermission),
    não pelos grupos globais do Django. Participação de projeto alheio responde 404.
    """

    queryset = Membership.objects.none()  # só para o schema/filtros descobrirem o model; o real é get_queryset
    permission_classes = [IsAuthenticated, MembershipRolePermission]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ('project', 'role')
    lookup_value_regex = r'\d+'

    def get_queryset(self):
        user = self.request.user
        my_role = Membership.objects.filter(project=OuterRef('project'), user=user).values('role')[:1]
        return (
            Membership.objects
            .filter(project__memberships__user=user)
            .annotate(my_role=Subquery(my_role))
            .select_related('user__profile_picture', 'project')
            .order_by('project_id', 'joined_at')
        )

    def get_serializer_class(self):
        if self.action == 'list':
            return MembershipListSerializer
        if self.action in {'update', 'partial_update'}:
            return MembershipCreateUpdateSerializer
        return MembershipRetrieveSerializer

    @transaction.atomic
    def perform_destroy(self, instance):
        if instance.role == Membership.Role.OWNER:
            raise ValidationError({'detail': 'O dono não pode ser removido. Transfira a propriedade antes.'})
        # Quem sai do projeto deixa de ser responsável pelas tarefas dele (responsável precisa ser participante).
        Task.objects.filter(project=instance.project, assignee=instance.user).update(assignee=None)
        instance.delete()
