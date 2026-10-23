from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.generics import CreateAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from core.models import User
from core.permissions import SelfUserPermission
from core.serializers import UserRegistrationSerializer, UserSerializer


class UserViewSet(ModelViewSet):
    queryset = User.objects.all().order_by('id')
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated, SelfUserPermission]

    def get_queryset(self):
        """Permissões de gestão enxergam todos; os demais, apenas a si mesmos."""
        queryset = super().get_queryset()
        user = self.request.user
        management_permissions = ('core.view_user', 'core.change_user', 'core.delete_user')

        if user.is_superuser or any(user.has_perm(permission) for permission in management_permissions):
            return queryset

        return queryset.filter(pk=user.pk)

    @extend_schema(
        summary='Dados do usuário autenticado',
        description='Retorna os dados do usuário autenticado.',
        responses={200: UserSerializer, 401: None},
    )
    @action(detail=False, methods=['get'], permission_classes=[IsAuthenticated])
    def me(self, request):
        """Retorna os dados do usuário autenticado."""
        user = request.user
        serializer = UserSerializer(user, context=self.get_serializer_context())
        return Response(serializer.data, status=status.HTTP_200_OK)

    def _check_user_permission(self, permission, *, allow_self):
        user = self.request.user
        target_user = self.get_object()

        # Só um superusuário pode alterar outro superusuário.
        if target_user.is_superuser and not user.is_superuser:
            return self._forbidden()

        if user.is_superuser or user.has_perm(permission) or (allow_self and user.id == target_user.id):
            return None

        return self._forbidden()

    @staticmethod
    def _forbidden():
        return Response(
            {'detail': 'Você não tem permissão para alterar este usuário.'},
            status=status.HTTP_403_FORBIDDEN,
        )

    def update(self, request, *args, **kwargs):
        response = self._check_user_permission('core.change_user', allow_self=True)

        if response:
            return response

        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        response = self._check_user_permission('core.change_user', allow_self=True)

        if response:
            return response

        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        response = self._check_user_permission('core.delete_user', allow_self=False)

        if response:
            return response

        return super().destroy(request, *args, **kwargs)


class UserRegistrationView(CreateAPIView):
    """Endpoint para registro de novos usuários."""

    queryset = User.objects.all()
    serializer_class = UserRegistrationSerializer
    permission_classes = [AllowAny]
