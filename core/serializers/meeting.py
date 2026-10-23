from rest_framework.exceptions import NotFound
from rest_framework.serializers import CharField, ModelSerializer, ValidationError

from core.models import Meeting, Membership
from core.serializers.support_link import http_url


class MeetingListSerializer(ModelSerializer):
    created_by_name = CharField(source='created_by.name', read_only=True, default=None)

    class Meta:
        model = Meeting
        fields = ('id', 'project', 'title', 'starts_at', 'ends_at', 'url', 'created_by', 'created_by_name')


class MeetingRetrieveSerializer(MeetingListSerializer):
    class Meta(MeetingListSerializer.Meta):
        fields = (*MeetingListSerializer.Meta.fields, 'description', 'created_at')


class MeetingCreateUpdateSerializer(ModelSerializer):
    """Agenda/edita reunião. created_by vem do perform_create; o projeto fica fixo; término >= início."""

    class Meta:
        model = Meeting
        fields = ('id', 'project', 'title', 'description', 'starts_at', 'ends_at', 'url', 'created_by', 'created_at')
        read_only_fields = ('created_by', 'created_at')
        extra_kwargs = {'url': {'validators': [http_url]}}

    def validate_project(self, project):
        if self.instance and project != self.instance.project:
            raise ValidationError('Não é possível mover a reunião para outro projeto.')
        if not Membership.objects.filter(project=project, user=self.context['request'].user).exists():
            raise NotFound('Projeto não encontrado.')
        return project

    def validate(self, attrs):
        # Num PATCH que muda só um dos lados, compara com o valor que já está salvo.
        starts_at = attrs.get('starts_at', getattr(self.instance, 'starts_at', None))
        ends_at = attrs['ends_at'] if 'ends_at' in attrs else getattr(self.instance, 'ends_at', None)
        if starts_at and ends_at and ends_at < starts_at:
            raise ValidationError({'ends_at': 'O término da reunião não pode ser antes do início.'})
        return attrs
