from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import GoogleIdentity
from core.serializers import (
    GoogleAuthErrorSerializer,
    GoogleLinkSerializer,
    GoogleLinkStatusSerializer,
    GoogleLoginSerializer,
    GoogleTokenPairSerializer,
    GroupTokenObtainPairSerializer,
)

ERRORS = {
    400: OpenApiResponse(description='Corpo inválido (falta "credential").'),
    401: OpenApiResponse(GoogleAuthErrorSerializer, description='invalid_google_token'),
    403: OpenApiResponse(GoogleAuthErrorSerializer, description='google_email_not_verified ou inactive_account'),
    503: OpenApiResponse(GoogleAuthErrorSerializer, description='google_unavailable ou google_auth_unavailable'),
}


def link_status(user):
    identity = GoogleIdentity.objects.filter(user=user).first()
    return {'linked': identity is not None, 'email': identity.email if identity else None}


class GoogleLoginView(APIView):
    """Entrar ou se cadastrar com Google. Devolve o mesmo JWT do login tradicional."""

    permission_classes = [AllowAny]
    # Público: um Authorization antigo/quebrado no navegador não pode impedir o login.
    authentication_classes = []

    @extend_schema(
        summary='Entrar/cadastrar com Google',
        description='Envie o ID token do Google Identity Services ({"credential": response.credential}). '
        'Conta vinculada: entra nela. Conta nova: cria usuário com o e-mail verificado do Google, sem senha. '
        'E-mail já usado por uma conta sem Google: 409 account_link_required (entre com senha e vincule).',
        request=GoogleLoginSerializer,
        responses={
            200: GoogleTokenPairSerializer,
            **ERRORS,
            409: OpenApiResponse(GoogleAuthErrorSerializer, description='account_link_required'),
        },
    )
    def post(self, request):
        serializer = GoogleLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        refresh = GroupTokenObtainPairSerializer.get_token(user)
        return Response({'access': str(refresh.access_token), 'refresh': str(refresh)}, status=status.HTTP_200_OK)


class GoogleLinkView(APIView):
    """Vínculo da conta Google com a conta Fluxo do usuário autenticado (JWT do Fluxo obrigatório)."""

    permission_classes = [IsAuthenticated]

    @extend_schema(summary='A conta Google está vinculada?', responses={200: GoogleLinkStatusSerializer})
    def get(self, request):
        return Response(link_status(request.user), status=status.HTTP_200_OK)

    @extend_schema(
        summary='Vincular conta Google à conta logada',
        description='Exige o JWT do Fluxo (Authorization: Bearer) e o ID token do Google. Depois disso a pessoa '
        'também entra pelo Google. Repetir com a mesma conta Google é inofensivo.',
        request=GoogleLinkSerializer,
        responses={
            200: GoogleLinkStatusSerializer,
            **ERRORS,
            409: OpenApiResponse(GoogleAuthErrorSerializer, description='google_account_in_use ou already_linked'),
        },
    )
    def post(self, request):
        serializer = GoogleLinkSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(link_status(request.user), status=status.HTTP_200_OK)
