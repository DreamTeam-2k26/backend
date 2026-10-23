from django.core.validators import URLValidator
from rest_framework.exceptions import NotFound
from rest_framework.serializers import CharField, ModelSerializer, ValidationError

from core.models import Membership, SupportLink

# Só http/https: barra javascript:, data:, ftp: e afins (o link é aberto direto pelo frontend).
http_url = URLValidator(schemes=['http', 'https'], message='Informe um link válido começando com http:// ou https://.')


class SupportLinkListSerializer(ModelSerializer):
    created_by_name = CharField(source='created_by.name', read_only=True, default=None)

    class Meta:
        model = SupportLink
        fields = ('id', 'project', 'task', 'title', 'url', 'created_by', 'created_by_name', 'created_at')


class SupportLinkRetrieveSerializer(SupportLinkListSerializer):
    task_title = CharField(source='task.title', read_only=True, default=None)

    class Meta(SupportLinkListSerializer.Meta):
        fields = (*SupportLinkListSerializer.Meta.fields, 'task_title')


class SupportLinkCreateUpdateSerializer(ModelSerializer):
    """Cria/edita link. created_by vem do perform_create; o projeto fica fixo e a tarefa é do mesmo projeto."""

    class Meta:
        model = SupportLink
        fields = ('id', 'project', 'task', 'title', 'url', 'created_by', 'created_at')
        read_only_fields = ('created_by', 'created_at')
        extra_kwargs = {'url': {'validators': [http_url]}}

    def validate_project(self, project):
        if self.instance and project != self.instance.project:
            raise ValidationError('Não é possível mover o link para outro projeto.')
        if not Membership.objects.filter(project=project, user=self.context['request'].user).exists():
            raise NotFound('Projeto não encontrado.')
        return project

    def validate(self, attrs):
        project = attrs.get('project') or self.instance.project
        task = attrs.get('task')
        if task is not None and task.project_id != project.pk:
            raise ValidationError({'task': 'A tarefa precisa ser do mesmo projeto do link.'})
        return attrs
