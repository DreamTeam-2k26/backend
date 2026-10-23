from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated

from chat import services
from chat.serializers import ChatUserSerializer


class UserSearchView(ListAPIView):
    """Busca de usuários para montar uma sala.

    Existe separado de /api/users/ porque lá usuários comuns só enxergam a si mesmos. Aqui o resultado
    traz apenas id, nome e foto (nunca e-mail ou permissões) e exige pelo menos 2 caracteres.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = ChatUserSerializer

    @extend_schema(
        summary='Buscar usuários por e-mail',
        parameters=[
            OpenApiParameter('search', str, description='Mínimo de 2 caracteres.'),
            OpenApiParameter('exclude_room', int, description='Omite quem já participa desta sala (se você participa dela).'),
        ],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        exclude_room = self.request.query_params.get('exclude_room')

        if exclude_room not in (None, ''):
            try:
                exclude_room = int(exclude_room)
            except ValueError as error:
                raise ValidationError({'exclude_room': 'Informe um id numérico.'}) from error
        else:
            exclude_room = None

        return services.search_users(self.request.user, self.request.query_params.get('search'), exclude_room)
