from django.utils.text import slugify
from rest_framework import serializers

from core.models import Group
from core.serializers.permission import PermissionSerializer


class GroupUsersSerializer(serializers.Serializer):
    """Corpo das ações de gestão de membros do grupo."""

    users = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        allow_empty=False,
    )


class GroupSerializer(serializers.ModelSerializer):
    """Serializador dos grupos (tipos de usuário)."""

    users_count = serializers.IntegerField(read_only=True)
    permissions_detail = PermissionSerializer(source='permissions', many=True, read_only=True)

    class Meta:
        model = Group
        fields = [
            'id',
            'name',
            'slug',
            'description',
            'is_default',
            'permissions',
            'permissions_detail',
            'users_count',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['created_at', 'updated_at']

    def __init__(self, *args, **kwargs):
        """Permissões concedem acesso: só superusuários podem alterá-las."""
        super().__init__(*args, **kwargs)

        request = self.context.get('request')
        user = getattr(request, 'user', None)

        if not (user and user.is_superuser):
            self.fields['permissions'].read_only = True

    def validate(self, attrs):
        """Gera o slug a partir do nome e garante um único grupo padrão."""
        attrs = super().validate(attrs)

        name = attrs.get('name') or getattr(self.instance, 'name', '')
        slug = attrs.get('slug') or slugify(name)

        if not slug:
            raise serializers.ValidationError({'slug': 'Informe um nome ou slug válido.'})

        attrs['slug'] = slug

        slug_taken = Group.objects.filter(slug=slug)

        if self.instance:
            slug_taken = slug_taken.exclude(pk=self.instance.pk)

        if slug_taken.exists():
            raise serializers.ValidationError({'slug': 'Este slug já está em uso.'})

        if attrs.get('is_default'):
            default_groups = Group.objects.filter(is_default=True)

            if self.instance:
                default_groups = default_groups.exclude(pk=self.instance.pk)

            if default_groups.exists():
                raise serializers.ValidationError({'is_default': 'Já existe um grupo padrão definido.'})

        return attrs
