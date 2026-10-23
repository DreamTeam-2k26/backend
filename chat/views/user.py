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
        summary='Buscar usuários por nome ou e-mail',
        parameters=[
            OpenApiParameter('search', str, description='Mínimo de 2 caracteres.'),
            OpenApiParameter('exclude_room', int, description='Omite quem já participa desta sala (se você participa dela).'),
            OpenApiParameter(
                'exclude_project', int, description='Omite quem já participa deste projeto ou já foi convidado.'
            ),
        ],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        params = self.request.query_params
        ids = {}

        for name in ('exclude_room', 'exclude_project'):
            value = params.get(name)

            if value in (None, ''):
                ids[name] = None
                continue

            try:
                ids[name] = int(value)
            except ValueError as error:
                raise ValidationError({name: 'Informe um id numérico.'}) from error

        return services.search_users(
            self.request.user, params.get('search'), ids['exclude_room'], ids['exclude_project']
        )
