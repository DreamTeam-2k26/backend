from django.contrib.auth import get_user_model
from rest_framework import serializers


# Valor padrão da migration de `core.User.name`: contas antigas ficaram com ele sem nunca terem escolhido um nome.
DEFAULT_NAME = 'usuário'


def display_name(user):
    """Nome exibido no chat: o nome do perfil; sem ele (ou com o padrão da migration), a parte local do e-mail."""
    name = (getattr(user, 'name', '') or '').strip()

    if name and name.casefold() != DEFAULT_NAME:
        return name

    email = getattr(user, 'email', '') or ''

    return email.split('@', maxsplit=1)[0] or 'Usuário'


class ChatUserSerializer(serializers.ModelSerializer):
    """Identificação pública mínima de um usuário no chat (nunca expõe e-mail nem permissões)."""

    name = serializers.SerializerMethodField()
    avatar = serializers.SerializerMethodField()

    class Meta:
        model = get_user_model()
        fields = ['id', 'name', 'avatar']
        read_only_fields = fields

    def get_name(self, user) -> str:
        return display_name(user)

    def get_avatar(self, user) -> str | None:
        image = user.profile_picture

        return image.url if image else None
