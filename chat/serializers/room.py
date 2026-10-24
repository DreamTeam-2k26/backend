from rest_framework import serializers

from chat.models import Membership
from core.models import Membership as ProjectMembership

from .message import MessageSerializer
from .user import ChatUserSerializer


class RoomSerializer(serializers.Serializer):
    """Resumo da sala para quem a consulta. Espera uma sala anotada por services.rooms_for."""

    id = serializers.IntegerField(read_only=True)
    name = serializers.CharField(read_only=True)
    image = serializers.SerializerMethodField()
    created_by = serializers.IntegerField(source='created_by_id', read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    last_message_at = serializers.DateTimeField(read_only=True)
    member_count = serializers.IntegerField(read_only=True)
    unread = serializers.IntegerField(read_only=True)
    is_admin = serializers.BooleanField(read_only=True)
    project = serializers.IntegerField(source='project_id', read_only=True)
    last_message = serializers.SerializerMethodField()

    def get_image(self, room) -> str | None:
        return room.image.url if room.image else None

    def get_last_message(self, room) -> dict | None:
        message = getattr(room, 'last_message', None)

        if message is None:
            return None

        return MessageSerializer(message, context=self.context).data


class RoomDetailSerializer(RoomSerializer):
    """Resumo da sala mais a lista de participantes (com presença)."""

    members = serializers.SerializerMethodField()

    def get_members(self, room) -> list:
        memberships = (
            Membership.objects.filter(room=room)
            .select_related('user__profile_picture', 'user__chat_presence')
            .order_by('-is_admin', 'user__email')
        )
        roles = dict(ProjectMembership.objects.filter(project_id=room.project_id).values_list('user_id', 'role'))
        data = []

        for membership in memberships:
            presence = getattr(membership.user, 'chat_presence', None)
            data.append({
                'user': ChatUserSerializer(membership.user).data,
                'is_admin': membership.is_admin,
                # Papel no projeto (owner, admin ou member); nulo nas salas avulsas antigas.
                'role': roles.get(membership.user_id),
                'joined_at': serializers.DateTimeField().to_representation(membership.joined_at),
                'online': bool(presence and presence.is_online),
                'last_seen': (
                    serializers.DateTimeField().to_representation(presence.last_seen)
                    if presence and presence.last_seen
                    else None
                ),
            })

        return data
