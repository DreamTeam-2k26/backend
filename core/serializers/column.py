from django.db import transaction
from django.db.models import Max
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.serializers import (
    IntegerField,
    ListField,
    ModelSerializer,
    PrimaryKeyRelatedField,
    Serializer,
    ValidationError,
)

from core.models import Column, Membership, Project


def check_board_manager(user, project):
    """Só dono/admin alteram as colunas. Fora do projeto: 404; sem papel: 403."""
    membership = Membership.objects.filter(project=project, user=user).first()
    if membership is None:
        raise NotFound('Projeto não encontrado.')
    if membership.role not in {Membership.Role.OWNER, Membership.Role.ADMIN}:
        raise PermissionDenied('Só o dono ou um administrador pode alterar as colunas do projeto.')


class ColumnListSerializer(ModelSerializer):
    class Meta:
        model = Column
        fields = ('id', 'project', 'name', 'category', 'position')


class ColumnRetrieveSerializer(ModelSerializer):
    class Meta:
        model = Column
        fields = ('id', 'project', 'name', 'category', 'position', 'created_at')


class ColumnCreateUpdateSerializer(ModelSerializer):
    """Cria, renomeia ou reposiciona uma coluna. Projeto e categoria interna ficam fixos depois de criada."""

    class Meta:
        model = Column
        fields = ('id', 'project', 'name', 'category', 'position')
        extra_kwargs = {'position': {'required': False}}

    def validate_project(self, project):
        if self.instance and project != self.instance.project:
            raise ValidationError('Não é possível mover a coluna para outro projeto.')
        check_board_manager(self.context['request'].user, project)
        return project

    def validate_category(self, category):
        if self.instance and category != self.instance.category:
            raise ValidationError('A categoria interna da coluna não pode ser alterada.')
        return category

    def create(self, validated_data):
        if 'position' not in validated_data:
            last = validated_data['project'].columns.aggregate(last=Max('position'))['last']
            validated_data['position'] = 0 if last is None else last + 1
        return super().create(validated_data)


class ColumnReorderSerializer(Serializer):
    """Nova ordem do quadro: ids de TODAS as colunas do projeto, cada uma uma vez."""

    project = PrimaryKeyRelatedField(queryset=Project.objects.all())
    columns = ListField(child=IntegerField(), allow_empty=False)

    def validate_project(self, project):
        check_board_manager(self.context['request'].user, project)
        return project

    def validate(self, attrs):
        ids = attrs['columns']
        project_ids = set(attrs['project'].columns.values_list('pk', flat=True))
        if len(ids) != len(set(ids)) or set(ids) != project_ids:
            raise ValidationError({'columns': 'Envie todas as colunas do projeto, cada uma uma única vez.'})
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        columns = {column.pk: column for column in validated_data['project'].columns.all()}
        for position, pk in enumerate(validated_data['columns']):
            columns[pk].position = position
        Column.objects.bulk_update(columns.values(), ['position'])
        return sorted(columns.values(), key=lambda column: column.position)
