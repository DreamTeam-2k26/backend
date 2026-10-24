from django.db.models import OuterRef, Subquery
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from core.models import BrainstormReply, Membership
from core.permissions import BrainstormReplyPermission
from core.serializers import (
    BrainstormReplyCreateUpdateSerializer,
    BrainstormReplyListSerializer,
    BrainstormVoteSerializer,
)
from core.serializers.brainstorm_topic import ensure_open

from .brainstorm_topic import VoteTotalsSerializer, with_votes


@extend_schema_view(
    list=extend_schema(summary='Respostas das discussões, da mais antiga à mais nova (filtre com ?topic=<id>)'),
    create=extend_schema(summary='Responder à discussão ou a outra resposta (parent); discussão aberta'),
    retrieve=extend_schema(summary='Detalhes da resposta'),
    update=extend_schema(summary='Editar resposta (somente o autor, discussão aberta)'),
    partial_update=extend_schema(summary='Editar resposta (somente o autor, discussão aberta)'),
    destroy=extend_schema(summary='Excluir resposta sem respostas filhas (autor, dono ou administrador)'),
)
class BrainstormReplyViewSet(ModelViewSet):
    """Respostas encadeadas do Brainstorm (parent monta a thread). Resposta de projeto alheio responde 404."""

    queryset = BrainstormReply.objects.none()  # só para o schema/filtros descobrirem o model; o real é get_queryset
    permission_classes = [IsAuthenticated, BrainstormReplyPermission]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ('topic', 'parent', 'author')
    lookup_value_regex = r'\d+'

    def get_queryset(self):
        user = self.request.user
        my_projects = Membership.objects.filter(user=user).values('project')
        my_role = Membership.objects.filter(project=OuterRef('topic__project'), user=user).values('role')[:1]
        queryset = (
            BrainstormReply.objects
            .filter(topic__project__in=my_projects)
            .annotate(my_role=Subquery(my_role))
            .select_related('author', 'topic')
            .order_by('topic_id', 'created_at', 'id')
        )
        return with_votes(queryset, user, 'reply')

    def get_serializer_class(self):
        if self.action in {'create', 'update', 'partial_update'}:
            return BrainstormReplyCreateUpdateSerializer
        if self.action == 'vote':
            return BrainstormVoteSerializer
        return BrainstormReplyListSerializer

    def perform_create(self, serializer):
        serializer.save(author=self.request.user)

    def perform_destroy(self, reply):
        ensure_open(reply.topic)
        if reply.children.exists():
            raise ValidationError('A resposta tem respostas e não pode ser excluída (a conversa ficaria quebrada).')
        reply.delete()

    @extend_schema(
        summary='Votar na resposta: 1, -1 ou 0 para remover (qualquer participante, discussão aberta)',
        request=BrainstormVoteSerializer,
        responses=VoteTotalsSerializer,
    )
    @action(detail=True, methods=['post'])
    def vote(self, request, pk=None):
        reply = self.get_object()
        serializer = BrainstormVoteSerializer(data=request.data, context={'topic': reply.topic})
        serializer.is_valid(raise_exception=True)
        return Response(serializer.save(user=request.user, target=reply))
