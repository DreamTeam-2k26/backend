from rest_framework import serializers

from chat.serializers.user import display_name

from . import services
from .models import TITLE_MAX, Call


def _person(user):
    return None if user is None else {'id': user.pk, 'name': display_name(user)}


class CallSerializer(serializers.ModelSerializer):
    """Chamada como o cliente enxerga. `participants_count` conta quem está dentro agora."""

    project_name = serializers.CharField(source='project.name', read_only=True)
    created_by = serializers.SerializerMethodField()
    participants_count = serializers.SerializerMethodField()
    can_manage = serializers.SerializerMethodField()

    class Meta:
        model = Call
        fields = (
            'id',
            'project',
            'project_name',
            'title',
            'description',
            'scheduled_for',
            'status',
            'created_by',
            'started_at',
            'ended_at',
            'created_at',
            'participants_count',
            'can_manage',
        )
        read_only_fields = fields

    def get_created_by(self, call) -> dict | None:
        return _person(call.created_by)

    def get_participants_count(self, call) -> int:
        counted = getattr(call, 'active_count', None)

        return counted if counted is not None else call.participants.filter(left_at__isnull=True).count()

    def get_can_manage(self, call) -> bool:
        request = self.context.get('request')

        return bool(request and services.is_manager(request.user, call.project_id))


class CallCreateSerializer(serializers.Serializer):
    """Cria uma chamada na hora (`scheduled_for` vazio) ou a agenda (`scheduled_for` no futuro)."""

    project = serializers.IntegerField()
    title = serializers.CharField(max_length=TITLE_MAX, required=False, allow_blank=True, default='')
    description = serializers.CharField(required=False, allow_blank=True, default='')
    scheduled_for = serializers.DateTimeField(required=False, allow_null=True, default=None)


class CallUpdateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=TITLE_MAX, required=False, allow_blank=True)
    description = serializers.CharField(required=False, allow_blank=True)
    scheduled_for = serializers.DateTimeField(required=False)
