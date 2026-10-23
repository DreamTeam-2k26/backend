from rest_framework_simplejwt.serializers import TokenObtainPairSerializer


class GroupTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Adiciona os grupos (tipos de usuário) e o status de equipe ao token JWT."""

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token['groups'] = [group.slug or group.name for group in user.groups.all()]
        token['is_staff'] = user.is_staff

        return token
