from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.serializers import ModelSerializer, SlugRelatedField

from core.models import User
from core.serializers.group import GroupSerializer
from uploader.models import Image
from uploader.serializers import ImageSerializer


def profile_picture_url(user) -> str | None:
    """URL da foto de perfil para exibir avatar em listas (null sem usuário ou sem foto)."""
    image = user.profile_picture if user else None
    return image.url if image else None


@extend_schema_field(OpenApiTypes.UUID)
class OwnImageField(SlugRelatedField):
    """Só aceita imagens enviadas pelo próprio usuário da requisição."""

    def get_queryset(self):
        request = self.context.get('request')
        user = getattr(request, 'user', None)

        if not (user and user.is_authenticated):
            return Image.objects.none()

        return Image.objects.filter(uploaded_by=user)


class UserSerializer(ModelSerializer):
    profile_picture_attachment_key = OwnImageField(
        source='profile_picture',
        slug_field='attachment_key',
        required=False,
        write_only=True,
        allow_null=True,
    )
    profile_picture = ImageSerializer(required=False, read_only=True)
    groups_detail = GroupSerializer(source='groups', many=True, read_only=True)

    class Meta:
        model = User
        fields = [
            'id',
            'email',
            'name',
            'is_active',
            'is_staff',
            'is_superuser',
            'last_login',
            'groups',
            'groups_detail',
            'profile_picture',
            'profile_picture_attachment_key',
        ]

    def __init__(self, *args, **kwargs):
        """Restringe os campos sensíveis conforme o privilégio de quem requisita."""
        super().__init__(*args, **kwargs)

        request = self.context.get('request')
        user = getattr(request, 'user', None)
        is_superuser = bool(user and user.is_superuser)
        is_manager = bool(user and (is_superuser or user.has_perm('core.change_user')))

        if not is_manager or self.context.get('profile_only'):
            for field in ('is_active', 'is_staff', 'is_superuser', 'last_login', 'groups', 'groups_detail'):
                self.fields.pop(field)
            return

        # Grupos concedem permissões, então só superusuários podem alterá-los.
        if not is_superuser:
            self.fields['is_staff'].read_only = True
            self.fields['is_superuser'].read_only = True
            self.fields['groups'].read_only = True


class UserRegistrationSerializer(ModelSerializer):
    password = serializers.CharField(write_only=True)
    profile_picture_attachment_key = OwnImageField(
        source='profile_picture', slug_field='attachment_key', required=False, write_only=True, allow_null=True
    )
    profile_picture = ImageSerializer(read_only=True)

    class Meta:
        model = User
        fields = ['id', 'email', 'name', 'password', 'profile_picture', 'profile_picture_attachment_key']
        read_only_fields = ['id']

    def validate(self, attrs):
        try:
            validate_password(attrs['password'], user=User(email=attrs.get('email')))
        except DjangoValidationError as error:
            raise serializers.ValidationError({'password': list(error.messages)}) from error

        return attrs

    def create(self, validated_data):
        return User.objects.create_user(**validated_data)
