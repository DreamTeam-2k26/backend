from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Count, IntegerField, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce
from django.http import Http404
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, status
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from core.models import Membership, Project

from . import conf, realtime, services, summarizer
from . import transcription as transcription_rules
from .models import Call, TranscriptSegment
from .serializers import (
    CallCreateSerializer,
    CallSerializer,
    CallUpdateSerializer,
    TranscriptionToggleSerializer,
    TranscriptSegmentSerializer,
)


def _run(function, *args, **kwargs):
    """Chama uma regra de services traduzindo os erros do Django para respostas da API."""
    try:
        return function(*args, **kwargs)
    except DjangoPermissionDenied as error:
        raise PermissionDenied(str(error) or None) from error
    except DjangoValidationError as error:
        detail = error.message_dict if hasattr(error, 'error_dict') else error.messages
        raise DRFValidationError(detail) from error


@extend_schema_view(
    list=extend_schema(summary='Chamadas dos seus projetos (filtre com ?project= e ?status=)'),
    retrieve=extend_schema(summary='Detalhes da chamada'),
    create=extend_schema(summary='Iniciar chamada agora (sem scheduled_for) ou agendar (dono/administrador)'),
    partial_update=extend_schema(summary='Editar chamada agendada (dono/administrador)'),
    destroy=extend_schema(summary='Cancelar chamada agendada ou remover encerrada (dono/administrador)'),
)
class CallViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    GenericViewSet,
):
    """Chamadas de voz/vídeo dos projetos em que o usuário participa (projeto alheio responde 404).

    Qualquer participante vê e entra (pelo WebSocket); iniciar, agendar, editar, encerrar e remover são
    do dono e dos administradores.
    """

    queryset = Call.objects.none()
    permission_classes = [IsAuthenticated]
    serializer_class = CallSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ('project', 'status')
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        projects = Membership.objects.filter(user=self.request.user).values('project')
        services.end_stale_calls()
        segments = (
            TranscriptSegment.objects.filter(call=OuterRef('pk')).order_by().values('call').annotate(total=Count('pk'))
        )

        return (
            Call.objects
            .filter(project__in=projects)
            .select_related('project', 'created_by')
            .annotate(
                active_count=Count('participants', filter=Q(participants__left_at__isnull=True)),
                segments_total=Coalesce(Subquery(segments.values('total'), output_field=IntegerField()), 0),
            )
            .order_by('-created_at', '-id')
        )

    def get_object(self):
        try:
            return super().get_object()
        except Http404 as error:
            raise NotFound('Chamada não encontrada.') from error

    def get_serializer_class(self):
        return {'create': CallCreateSerializer, 'partial_update': CallUpdateSerializer}.get(self.action, CallSerializer)

    def _out(self, call):
        call = self.get_queryset().get(pk=call.pk)

        return CallSerializer(call, context=self.get_serializer_context()).data

    def create(self, request, *args, **kwargs):
        serializer = CallCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        project = Project.objects.filter(pk=data['project'], memberships__user=request.user).first()

        if project is None:
            raise NotFound('Projeto não encontrado.')

        if data['scheduled_for'] is None:
            call = _run(services.start_now, project, request.user, data['title'])
        else:
            call = _run(
                services.schedule, project, request.user, data['title'], data['scheduled_for'], data['description']
            )

        realtime.broadcast_call_changed(call)

        return Response(self._out(call), status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        call = self.get_object()
        serializer = CallUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        _run(services.update_scheduled, call, request.user, **serializer.validated_data)
        realtime.broadcast_call_changed(call)

        return Response(self._out(call))

    def destroy(self, request, *args, **kwargs):
        call = self.get_object()
        snapshot = Call(
            pk=call.pk, project=call.project, title=call.title, status=call.status, created_at=call.created_at
        )
        _run(services.cancel, call, request.user)
        realtime.broadcast_call_changed(snapshot, removed=True)

        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(summary='Iniciar agora uma chamada agendada (dono/administrador)', request=None)
    @action(detail=True, methods=['post'])
    def start(self, request, pk=None):
        call = self.get_object()
        _run(services.start_scheduled, call, request.user)
        realtime.broadcast_call_changed(call)

        return Response(self._out(call))

    @extend_schema(summary='Encerrar a chamada para todos (dono/administrador)', request=None)
    @action(detail=True, methods=['post'])
    def end(self, request, pk=None):
        call = self.get_object()
        _run(services.end_call, call, request.user)
        realtime.broadcast_call_ended(call.pk)
        realtime.broadcast_call_changed(call)

        return Response(self._out(call))

    @extend_schema(summary='Chamadas em andamento dos seus projetos', responses={200: CallSerializer(many=True)})
    @action(detail=False, methods=['get'])
    def live(self, request):
        queryset = self.get_queryset().filter(status=Call.Status.LIVE)

        return Response(CallSerializer(queryset, many=True, context=self.get_serializer_context()).data)

    @extend_schema(summary='Servidores ICE (STUN/TURN) e limite de participantes', responses={200: dict})
    @action(detail=False, methods=['get'])
    def config(self, request):
        return Response({'ice_servers': conf.ice_servers(), 'max_participants': conf.max_participants()})

    @extend_schema(
        summary='Ligar/desligar a transcrição durante a chamada (dono/administrador)',
        request=TranscriptionToggleSerializer,
        responses={200: CallSerializer},
    )
    @action(detail=True, methods=['post'])
    def transcription(self, request, pk=None):
        call = self.get_object()
        serializer = TranscriptionToggleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        _run(transcription_rules.set_enabled, call, request.user, serializer.validated_data['enabled'])
        realtime.broadcast_transcription_changed(call, request.user.pk)
        realtime.broadcast_call_changed(call)

        return Response(self._out(call))

    @extend_schema(summary='Transcrição da reunião (?after=<id> devolve só as falas novas)', responses={200: dict})
    @action(detail=True, methods=['get'])
    def transcript(self, request, pk=None):
        call = self.get_object()
        after = request.query_params.get('after', '0')
        after = int(after) if after.isdigit() else 0
        segments = TranscriptSegmentSerializer(transcription_rules.segments_for(call, after), many=True).data

        return Response({
            'call': call.pk,
            'transcription_on': call.transcription_on and call.status == Call.Status.LIVE,
            'segments': segments,
            'summary': call.summary,
            'summary_at': call.summary_at,
            'summary_available': summarizer.is_available(),
        })

    @extend_schema(summary='Gerar o resumo da reunião com IA (dono/administrador)', request=None, responses={200: dict})
    @action(detail=True, methods=['post'])
    def summary(self, request, pk=None):
        call = self.get_object()
        _run(services.require_manager, request.user, call.project_id)

        try:
            _run(summarizer.summarize, call)
        except summarizer.SummaryUnavailableError as error:
            return Response({'detail': str(error)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        return Response({'summary': call.summary, 'summary_at': call.summary_at})
