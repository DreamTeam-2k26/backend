from django.db.models import OuterRef, Subquery
from django.utils import timezone
from django_filters.rest_framework import BooleanFilter, DjangoFilterBackend, FilterSet
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from core.models import Meeting, Membership
from core.permissions import MeetingPermission
from core.serializers import MeetingCreateUpdateSerializer, MeetingListSerializer, MeetingRetrieveSerializer


class MeetingFilter(FilterSet):
    upcoming = BooleanFilter(method='filter_upcoming', label='true: ainda não começaram; false: já começaram')

    class Meta:
        model = Meeting
        fields = {'project': ['exact'], 'starts_at': ['gte', 'lte', 'date']}

    def filter_upcoming(self, queryset, name, value):
        now = timezone.now()
        return queryset.filter(starts_at__gte=now) if value else queryset.filter(starts_at__lt=now)


@extend_schema_view(
    list=extend_schema(
        summary='Reuniões por data de início (filtre com ?project=, ?starts_at__gte/lte=, ?starts_at__date=, '
        '?upcoming=true)'
    ),
    create=extend_schema(summary='Agendar reunião (qualquer participante; autor vem do token)'),
    retrieve=extend_schema(summary='Detalhes da reunião'),
    update=extend_schema(summary='Editar reunião (quem criou, dono ou administrador)'),
    partial_update=extend_schema(summary='Editar reunião (quem criou, dono ou administrador)'),
    destroy=extend_schema(summary='Excluir reunião (quem criou, dono ou administrador)'),
)
class MeetingViewSet(ModelViewSet):
    """Reuniões dos projetos do usuário autenticado, com link externo (sem videoconferência nativa).

    Qualquer participante vê e agenda; editar/excluir é de quem criou ou de dono/admin (MeetingPermission).
    Reunião de projeto alheio responde 404. Datas saem em ISO 8601 com fuso.
    """

    queryset = Meeting.objects.none()  # só para o schema/filtros descobrirem o model; o real é get_queryset
    permission_classes = [IsAuthenticated, MeetingPermission]
    filter_backends = [DjangoFilterBackend]
    filterset_class = MeetingFilter
    lookup_value_regex = r'\d+'

    def get_queryset(self):
        user = self.request.user
        my_projects = Membership.objects.filter(user=user).values('project')
        my_role = Membership.objects.filter(project=OuterRef('project'), user=user).values('role')[:1]
        return (
            Meeting.objects
            .filter(project__in=my_projects)
            .annotate(my_role=Subquery(my_role))
            .select_related('created_by')
            .order_by('starts_at', 'id')
        )

    def get_serializer_class(self):
        if self.action == 'list':
            return MeetingListSerializer
        if self.action in {'create', 'update', 'partial_update'}:
            return MeetingCreateUpdateSerializer
        return MeetingRetrieveSerializer

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)
