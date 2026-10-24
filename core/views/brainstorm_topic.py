from django.db.models import Count, F, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiExample, extend_schema, extend_schema_view, inline_serializer
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.serializers import CharField, IntegerField
from rest_framework.viewsets import ModelViewSet

from core import ai
from core.models import BrainstormTopic, BrainstormVote, Membership
from core.permissions import BrainstormTopicPermission
from core.serializers import (
    BrainstormAnalysisSerializer,
    BrainstormAnalyzeSerializer,
    BrainstormApproveSerializer,
    BrainstormDecisionSerializer,
    BrainstormTopicCreateUpdateSerializer,
    BrainstormTopicListSerializer,
    BrainstormTopicRetrieveSerializer,
    BrainstormVersionSerializer,
    BrainstormVoteSerializer,
)
from core.serializers.brainstorm_analysis import InvalidAnalysis

Status = BrainstormTopic.Status

ANALYSIS_EXAMPLE = OpenApiExample(
    'Síntese pronta',
    response_only=True,
    status_codes=['201', '200'],
    value={
        'id': 7,
        'topic': 12,
        'cycle': 1,
        'status': 'ready',
        'result': {
            'main_solution': 'Tema da feira: robótica sustentável.',
            'reasoning': 'Une as duas propostas mais votadas (R31 e R34).',
            'alternatives': [{'title': 'Só robótica', 'description': 'Menos trabalho de pesquisa.', 'sources': [31]}],
            'sources': [31, 34],
            'ai_additions': ['Convidar um professor de física para validar o protótipo.'],
            'task_suggestions': [
                {
                    'title': 'Montar protótipo com sucata',
                    'description': 'Separar materiais e montar a base.',
                    'priority': 'high',
                    'existing_task_id': None,
                    'sources': [34],
                },
                {
                    'title': 'Comprar motores',
                    'description': '',
                    'priority': 'medium',
                    'existing_task_id': 42,
                    'sources': [],
                },
            ],
        },
        'model': 'google/gemma-4-31b-it:free',
        'error': '',
        'requested_by': 1,
        'requested_by_name': 'Ana',
        'created_at': '2026-10-24T14:00:00-03:00',
        'finished_at': '2026-10-24T14:00:12-03:00',
    },
)
APPROVE_EXAMPLE = OpenApiExample(
    'Aprovar com uma tarefa nova e uma existente',
    request_only=True,
    value={
        'analysis': 7,
        'text': 'Faremos robótica sustentável.',
        'justification': 'Proposta mais votada.',
        'tasks': [
            {
                'suggestion_index': 0,
                'title': 'Montar protótipo com sucata',
                'description': 'Separar materiais e montar a base.',
                'priority': 'high',
                'assignee': 3,
                'due_date': '2026-11-01',
            },
            {'existing_task_id': 42},
        ],
    },
)
AI_ERROR_EXAMPLE = OpenApiExample(
    'Servidor sem chave',
    response_only=True,
    status_codes=['503'],
    value={'detail': 'A IA não está configurada neste servidor.', 'code': 'ai_not_configured'},
)

AIErrorSerializer = inline_serializer(
    'BrainstormAIError',
    fields={'detail': CharField(), 'code': CharField(), 'analysis': IntegerField(required=False)},
)

VoteTotalsSerializer = inline_serializer(
    'BrainstormVoteTotals',
    fields={'score': IntegerField(), 'upvotes': IntegerField(), 'downvotes': IntegerField(), 'my_vote': IntegerField()},
)


def with_votes(queryset, user, target):
    """Anota upvotes, downvotes, score e my_vote (1, -1 ou 0) dos votos ligados por ``target`` (topic ou reply)."""
    my_vote = BrainstormVote.objects.filter(**{target: OuterRef('pk')}, user=user).values('value')[:1]
    return queryset.annotate(
        upvotes=Count('votes', filter=Q(votes__value=1), distinct=True),
        downvotes=Count('votes', filter=Q(votes__value=-1), distinct=True),
        score=F('upvotes') - F('downvotes'),
        my_vote=Coalesce(Subquery(my_vote), Value(0)),
    )


@extend_schema_view(
    list=extend_schema(
        summary='Discussões do Brainstorm (filtre com ?project=, ?category=, ?status=; busque com ?search=; '
        'ordene com ?ordering=-created_at|-score|-replies_count)'
    ),
    create=extend_schema(summary='Abrir discussão (qualquer participante; autor vem do token)'),
    retrieve=extend_schema(summary='Detalhes da discussão, com a decisão vigente'),
    update=extend_schema(summary='Editar discussão aberta (somente o autor)'),
    partial_update=extend_schema(summary='Editar discussão aberta (somente o autor)'),
    destroy=extend_schema(
        summary='Excluir discussão sem interações (autor, dono ou administrador); com interações, arquive'
    ),
)
class BrainstormTopicViewSet(ModelViewSet):
    """Fórum do Brainstorm dos projetos do usuário autenticado. Discussão de projeto alheio responde 404.

    Regras de papel na BrainstormTopicPermission; respostas, votos e edição exigem a discussão aberta.
    """

    queryset = BrainstormTopic.objects.none()  # só para o schema/filtros descobrirem o model; o real é get_queryset
    permission_classes = [IsAuthenticated, BrainstormTopicPermission]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ('project', 'category', 'status')
    search_fields = ('title', 'context')
    ordering_fields = ('created_at', 'score', 'replies_count')
    ordering = ('-created_at', '-id')
    lookup_value_regex = r'\d+'

    def get_queryset(self):
        user = self.request.user
        my_projects = Membership.objects.filter(user=user).values('project')
        my_role = Membership.objects.filter(project=OuterRef('project'), user=user).values('role')[:1]
        queryset = (
            BrainstormTopic.objects
            .filter(project__in=my_projects)
            .annotate(my_role=Subquery(my_role), replies_count=Count('replies', distinct=True))
            .select_related('author', 'current_decision__approved_by')
        )
        return with_votes(queryset, user, 'topic')

    def get_serializer_class(self):
        if self.action == 'list':
            return BrainstormTopicListSerializer
        if self.action in {'create', 'update', 'partial_update'}:
            return BrainstormTopicCreateUpdateSerializer
        if self.action == 'vote':
            return BrainstormVoteSerializer
        return BrainstormTopicRetrieveSerializer

    def perform_create(self, serializer):
        serializer.save(author=self.request.user)

    def perform_destroy(self, topic):
        if topic.has_interactions():
            raise ValidationError(
                'A discussão já tem respostas, votos, análise ou decisão. Arquive para preservar o histórico.'
            )
        topic.delete()

    def detail_response(self, topic):
        """Relê a discussão com as anotações (contadores, voto, papel) depois de uma ação."""
        return Response(BrainstormTopicRetrieveSerializer(self.get_queryset().get(pk=topic.pk)).data)

    def change_status(self, allowed, message, **fields):
        topic = self.get_object()
        if topic.status not in allowed:
            raise ValidationError(message)
        for name, value in fields.items():
            setattr(topic, name, value(topic) if callable(value) else value)
        topic.save(update_fields=[*fields, 'updated_at'])
        return self.detail_response(topic)

    @extend_schema(
        summary='Encerrar o debate (dono/administrador)', request=None, responses=BrainstormTopicRetrieveSerializer
    )
    @action(detail=True, methods=['post'])
    def close(self, request, pk=None):
        return self.change_status(
            {Status.OPEN},
            'Só é possível encerrar uma discussão aberta.',
            status=Status.CLOSED,
            closed_at=timezone.now(),
        )

    @extend_schema(
        summary='Reabrir o debate (dono/administrador): nova versão; a decisão anterior segue vigente "Em revisão"',
        request=None,
        responses=BrainstormTopicRetrieveSerializer,
    )
    @action(detail=True, methods=['post'])
    def reopen(self, request, pk=None):
        return self.change_status(
            {Status.CLOSED, Status.DECIDED},
            'Só é possível reabrir uma discussão encerrada ou decidida.',
            status=Status.OPEN,
            closed_at=None,
            cycle=lambda topic: topic.cycle + 1,
        )

    @extend_schema(
        summary='Arquivar (dono/administrador): somente leitura, definitivo',
        request=None,
        responses=BrainstormTopicRetrieveSerializer,
    )
    @action(detail=True, methods=['post'])
    def archive(self, request, pk=None):
        return self.change_status(
            {Status.OPEN, Status.CLOSED, Status.DECIDED},
            'A discussão já está arquivada.',
            status=Status.ARCHIVED,
            archived_at=timezone.now(),
        )

    @extend_schema(
        summary='Votar na discussão: 1, -1 ou 0 para remover (qualquer participante, discussão aberta)',
        request=BrainstormVoteSerializer,
        responses=VoteTotalsSerializer,
    )
    @action(detail=True, methods=['post'])
    def vote(self, request, pk=None):
        topic = self.get_object()
        serializer = BrainstormVoteSerializer(data=request.data, context={'topic': topic})
        serializer.is_valid(raise_exception=True)
        return Response(serializer.save(user=request.user, target=topic))

    @extend_schema(
        summary='Pedir a síntese da IA (dono/administrador, discussão encerrada). Roda na hora (até 60 s).',
        description=(
            'Cria uma análise nova do ciclo atual (as anteriores são preservadas). Erros: 503 ai_not_configured '
            '(servidor sem AI_API_KEY, nada é gravado), 503 ai_unavailable e 502 ai_invalid_response '
            '(a análise fica gravada como failed). A discussão nunca é alterada.'
        ),
        request=None,
        responses={201: BrainstormAnalysisSerializer, 502: AIErrorSerializer, 503: AIErrorSerializer},
        examples=[ANALYSIS_EXAMPLE, AI_ERROR_EXAMPLE],
    )
    @action(detail=True, methods=['post'])
    def analyze(self, request, pk=None):
        topic = self.get_object()
        serializer = BrainstormAnalyzeSerializer(data={}, context={'topic': topic})
        try:
            serializer.is_valid(raise_exception=True)
            analysis = serializer.save(requested_by=request.user)
        except ai.AINotConfigured as error:
            return ai_error(error, 'ai_not_configured', status.HTTP_503_SERVICE_UNAVAILABLE)
        except ai.AIError as error:
            return ai_error(error, 'ai_unavailable', status.HTTP_503_SERVICE_UNAVAILABLE, topic)
        except InvalidAnalysis:
            message = 'A IA respondeu fora do formato esperado. Tente novamente.'
            return ai_error(message, 'ai_invalid_response', status.HTTP_502_BAD_GATEWAY, topic)
        return Response(BrainstormAnalysisSerializer(analysis).data, status=status.HTTP_201_CREATED)

    @extend_schema(
        summary='Síntese mais recente do ciclo atual (qualquer participante; 404 se ainda não houver)',
        responses=BrainstormAnalysisSerializer,
        examples=[ANALYSIS_EXAMPLE],
    )
    @action(detail=True, methods=['get'])
    def analysis(self, request, pk=None):
        topic = self.get_object()
        latest = topic.analyses.filter(cycle=topic.cycle).select_related('requested_by').first()
        if latest is None:
            raise NotFound('Ainda não há análise neste ciclo da discussão.')
        return Response(BrainstormAnalysisSerializer(latest).data)

    @extend_schema(
        summary='Aprovar a decisão do ciclo (dono/administrador) e criar/vincular as tarefas escolhidas',
        description=(
            'Exige a discussão encerrada e uma análise pronta do ciclo atual. Cada item de tasks cria uma tarefa '
            'nova na coluna "A fazer" (mesmas regras do Kanban) ou vincula existing_task_id do mesmo projeto. '
            'tasks vazio não cria tarefas. Atômico; reenviar com a mesma análise devolve 200 com a decisão já '
            'gravada (sem duplicar tarefas); outra análise no mesmo ciclo responde 409.'
        ),
        request=BrainstormApproveSerializer,
        responses={201: BrainstormDecisionSerializer, 200: BrainstormDecisionSerializer},
        examples=[APPROVE_EXAMPLE],
    )
    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        topic = self.get_object()
        serializer = BrainstormApproveSerializer(data=request.data, context={'topic': topic, 'request': request})
        serializer.is_valid(raise_exception=True)
        decision = serializer.save()
        return Response(
            BrainstormDecisionSerializer(decision).data,
            status=status.HTTP_200_OK if serializer.replayed else status.HTTP_201_CREATED,
        )

    @extend_schema(
        summary='Versões da discussão: análises e decisão aprovada de cada ciclo (qualquer participante)',
        responses=BrainstormVersionSerializer(many=True),
    )
    @action(detail=True, methods=['get'])
    def versions(self, request, pk=None):
        topic = self.get_object()
        analyses = list(topic.analyses.order_by('created_at', 'id'))
        decisions = {
            decision.cycle: decision
            for decision in topic.decisions.select_related('approved_by').prefetch_related('task_links')
        }
        versions = [
            {
                'cycle': cycle,
                'current': cycle in decisions and decisions[cycle].pk == topic.current_decision_id,
                'analyses': [item for item in analyses if item.cycle == cycle],
                'decision': decisions.get(cycle),
            }
            for cycle in range(1, topic.cycle + 1)
        ]
        return Response(BrainstormVersionSerializer(versions, many=True).data)


def ai_error(message, code, http_status, topic=None):
    body = {'detail': str(message), 'code': code}
    if topic is not None:
        failed = topic.analyses.first()
        if failed is not None:
            body['analysis'] = failed.pk
    return Response(body, status=http_status)
