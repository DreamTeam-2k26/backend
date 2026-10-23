from rest_framework_simplejwt.views import TokenObtainPairView

from core.serializers import GroupTokenObtainPairSerializer


class GroupTokenObtainPairView(TokenObtainPairView):
    """Login que devolve um token JWT com os grupos (tipos de usuário) do usuário."""

    serializer_class = GroupTokenObtainPairSerializer
