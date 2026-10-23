from django.db.models import OuterRef, Q, Subquery
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.mixins import CreateModelMixin, ListModelMixin, RetrieveModelMixin
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from core.models import Invite, Membership, User
from core.permissions import InvitePermission
from core.serializers import (
    InviteAcceptSerializer,
    InviteCreateSerializer,
    InviteDeclineSerializer,
    InviteFindUserSerializer,
    InviteListSerializer,
    InviteReceivedSerializer,
    InviteRedeemSerializer,
    InviteRetrieveSerializer,
    InviteRevokeSerializer,
    InviteUserSerializer,
    MembershipRetrieveSerializer,
)


@extend_schema_view(
    list=extend_schema(summary='Convites dos projetos que você administra e os que você recebeu'),
    create=extend_schema(summary='Convidar (direct: usuário cadastrado; link: token gerado pelo servidor)'),
    retrieve=extend_schema(summary='Detalhes do convite (dono/admin do projeto ou destinatário)'),
)
class InviteViewSet(CreateModelMixin, ListModelMixin, RetrieveModelMixin, GenericViewSet):
    """Convites para projetos.

    Convite não é editado (sem PUT/PATCH): projeto, tipo e papel ficam fixos; muda só o status pelas ações.
    Autorização pela participação no projeto (InvitePermission), não pelos grupos globais do Django.
    Convite de projeto alheio (e que não é seu) responde 404.
    """

    queryset = Invite.objects.none()  # só para o schema/filtros descobrirem o model; o real é get_queryset
    permission_classes = [IsAuthenticated, InvitePermission]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ('project', 'status', 'kind')
    lookup_value_regex = r'\d+'

    def get_queryset(self):
        user = self.request.user
        my_projects = Membership.objects.filter(user=user).values('project')
        my_role = Membership.objects.filter(project=OuterRef('project'), user=user).values('role')[:1]
        queryset = (
            Invite.objects
            .filter(Q(project__in=my_projects) | Q(invited_user=user))
            .annotate(my_role=Subquery(my_role))
            .select_related('project', 'invited_user', 'invited_by')
            .order_by('-created_at')
        )
        if self.action == 'list':
            # Membro comum não lista convites do projeto (nem tokens de link); só os que recebeu.
            queryset = queryset.filter(
                Q(my_role__in=(Membership.Role.OWNER, Membership.Role.ADMIN)) | Q(invited_user=user)
            )
        return queryset

    def get_serializer_class(self):
        if self.action == 'list':
            return InviteListSerializer
        if self.action == 'create':
            return InviteCreateSerializer
        return InviteRetrieveSerializer

    def perform_create(self, serializer):
        serializer.save(invited_by=self.request.user)

    def _change_status(self, request, serializer_class):
        invite = self.get_object()
        serializer = serializer_class(data=request.data, context={**self.get_serializer_context(), 'invite': invite})
        serializer.is_valid(raise_exception=True)
        return serializer.save()

    @extend_schema(
        summary='Aceitar convite direto (somente o convidado)',
        request=None,
        responses={200: MembershipRetrieveSerializer},
    )
    @action(detail=True, methods=['post'])
    def accept(self, request, pk=None):
        membership = self._change_status(request, InviteAcceptSerializer)
        return Response(MembershipRetrieveSerializer(membership).data, status=status.HTTP_200_OK)

    @extend_schema(
        summary='Recusar convite direto (somente o convidado)',
        request=None,
        responses={200: InviteRetrieveSerializer},
    )
    @action(detail=True, methods=['post'])
    def decline(self, request, pk=None):
        invite = self._change_status(request, InviteDeclineSerializer)
        return Response(InviteRetrieveSerializer(invite).data, status=status.HTTP_200_OK)

    @extend_schema(
        summary='Revogar convite pendente (dono ou administrador)',
        request=None,
        responses={200: InviteRetrieveSerializer},
    )
    @action(detail=True, methods=['post'])
    def revoke(self, request, pk=None):
        invite = self._change_status(request, InviteRevokeSerializer)
        return Response(InviteRetrieveSerializer(invite).data, status=status.HTTP_200_OK)

    @extend_schema(
        summary='Entrar no projeto por link (o link continua válido para outras pessoas)',
        request=InviteRedeemSerializer,
        responses={200: MembershipRetrieveSerializer},
    )
    @action(detail=False, methods=['post'])
    def redeem(self, request):
        serializer = InviteRedeemSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        membership = serializer.save()
        return Response(MembershipRetrieveSerializer(membership).data, status=status.HTTP_200_OK)

    @extend_schema(
        summary='Meus convites diretos pendentes',
        responses={200: InviteReceivedSerializer(many=True)},
    )
    @action(detail=False, methods=['get'])
    def received(self, request):
        invites = (
            Invite.objects
            .filter(invited_user=request.user, kind=Invite.Kind.DIRECT, status=Invite.Status.PENDING)
            .exclude(expires_at__lte=timezone.now())
            .select_related('project', 'invited_by')
            .order_by('-created_at')
        )
        return Response(InviteReceivedSerializer(invites, many=True).data, status=status.HTTP_200_OK)

    @extend_schema(
        summary='Localizar usuário cadastrado para convidar (e-mail exato; dono ou administrador)',
        parameters=[
            OpenApiParameter('project', int, required=True),
            OpenApiParameter('email', str, required=True, description='E-mail completo do usuário.'),
        ],
        responses={200: InviteUserSerializer(many=True)},
    )
    @action(detail=False, methods=['get'])
    def find_user(self, request):
        serializer = InviteFindUserSerializer(data=request.query_params, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        users = User.objects.filter(email__iexact=serializer.validated_data['email'], is_active=True).exclude(
            project_memberships__project=serializer.validated_data['project']
        )
        return Response(InviteUserSerializer(users, many=True).data, status=status.HTTP_200_OK)
