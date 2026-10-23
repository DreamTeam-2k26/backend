from django.contrib.auth import get_user_model
from rest_framework import serializers

from chat.models import Membership
from chat.models.room import ROOM_NAME_MAX
from core.serializers.user import OwnImageField

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
        data = []

        for membership in memberships:
            presence = getattr(membership.user, 'chat_presence', None)
            data.append({
                'user': ChatUserSerializer(membership.user).data,
                'is_admin': membership.is_admin,
                'joined_at': serializers.DateTimeField().to_representation(membership.joined_at),
                'online': bool(presence and presence.is_online),
                'last_seen': (
                    serializers.DateTimeField().to_representation(presence.last_seen)
                    if presence and presence.last_seen
                    else None
                ),
            })

        return data


class ActiveUsersField(serializers.PrimaryKeyRelatedField):
    """Lista de ids de usuários ativos (ids inexistentes ou inativos viram erro de validação)."""

    def get_queryset(self):
        return get_user_model().objects.filter(is_active=True)


class RoomCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=ROOM_NAME_MAX)
    members = ActiveUsersField(many=True, required=False, default=list)
    image_attachment_key = OwnImageField(
        source='image', slug_field='attachment_key', required=False, allow_null=True
    )


class RoomUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=ROOM_NAME_MAX, required=False)
    image_attachment_key = OwnImageField(
        source='image', slug_field='attachment_key', required=False, allow_null=True
    )


class RoomMembersSerializer(serializers.Serializer):
    users = ActiveUsersField(many=True, allow_empty=False)


class RoomAdminSerializer(serializers.Serializer):
    is_admin = serializers.BooleanField()
