from rest_framework.exceptions import NotFound
from rest_framework.serializers import (
    CharField,
    IntegerField,
    ModelSerializer,
    SerializerMethodField,
    ValidationError,
)

from core.models import Folder, Membership


class FolderListSerializer(ModelSerializer):
    children_count = IntegerField(read_only=True)  # anotado no get_queryset da FolderViewSet

    class Meta:
        model = Folder
        fields = ('id', 'project', 'parent', 'name', 'children_count', 'created_at')


class FolderRetrieveSerializer(ModelSerializer):
    children_count = IntegerField(read_only=True)
    created_by_name = CharField(source='created_by.name', read_only=True, default=None)
    path = SerializerMethodField(help_text='Pastas da raiz até esta, para o breadcrumb.')

    class Meta:
        model = Folder
        fields = (
            'id',
            'project',
            'parent',
            'name',
            'children_count',
            'path',
            'created_by',
            'created_by_name',
            'created_at',
        )

    def get_path(self, folder) -> list[dict]:
        return [{'id': item.pk, 'name': item.name} for item in folder.ancestors_and_self()]


class FolderCreateUpdateSerializer(ModelSerializer):
    """Cria, renomeia ou move (parent) uma pasta. created_by vem do perform_create; o projeto fica fixo."""

    class Meta:
        model = Folder
        fields = ('id', 'project', 'parent', 'name', 'created_by', 'created_at')
        read_only_fields = ('created_by', 'created_at')

    def validate_project(self, project):
        if self.instance and project != self.instance.project:
            raise ValidationError('Não é possível mover a pasta para outro projeto.')
        if not Membership.objects.filter(project=project, user=self.context['request'].user).exists():
            raise NotFound('Projeto não encontrado.')
        return project

    def validate(self, attrs):
        project = attrs.get('project') or self.instance.project
        parent = attrs['parent'] if 'parent' in attrs else getattr(self.instance, 'parent', None)
        name = attrs.get('name', getattr(self.instance, 'name', None))

        if parent is not None:
            if parent.project_id != project.pk:
                raise ValidationError({'parent': 'A pasta pai precisa ser do mesmo projeto.'})
            # Se a própria pasta está no caminho da nova pasta pai até a raiz, o movimento criaria um ciclo.
            if self.instance is not None and any(
                folder.pk == self.instance.pk for folder in parent.ancestors_and_self()
            ):
                raise ValidationError({
                    'parent': 'Não é possível mover uma pasta para dentro dela mesma ou de uma subpasta.'
                })

        siblings = Folder.objects.filter(project=project, parent=parent, name__iexact=name)
        if self.instance is not None:
            siblings = siblings.exclude(pk=self.instance.pk)
        if siblings.exists():
            raise ValidationError({'name': 'Já existe uma pasta com esse nome neste local.'})
        return attrs
