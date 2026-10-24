from rest_framework import serializers

from chat.models import Message
from chat.models.message import MESSAGE_MAX

from .user import ChatUserSerializer

PREVIEW_SIZE = 80


class MessageSerializer(serializers.ModelSerializer):
    """Mensagem no formato único usado pela API REST e pelos eventos WebSocket.

    `read` indica leitura por todos os demais participantes; quem serializa informa os ids
    em `context['read_ids']` (calculados em lote por services.read_by_all_ids).
    """

    author = ChatUserSerializer(read_only=True)
    file = serializers.SerializerMethodField()
    reply_to = serializers.SerializerMethodField()
    read = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = ['id', 'room', 'author', 'body', 'file', 'created', 'edited_at', 'is_deleted', 'reply_to', 'read']
        read_only_fields = fields

    def get_file(self, message) -> dict | None:
        # O arquivo só é baixado por GET /chat/messages/{id}/file/ (autorizado); o caminho no storage nunca sai.
        if not message.file:
            return None

        return {
            'name': message.file_name,
            'size': message.file_size,
            'content_type': message.file_content_type,
            'is_image': message.is_image,
        }

    def get_reply_to(self, message) -> dict | None:
        original = message.reply_to

        if original is None:
            return None

        return {
            'id': original.id,
            'author': ChatUserSerializer(original.author).data if original.author else None,
            'body': None if original.is_deleted else (original.body or '')[:PREVIEW_SIZE],
            'has_file': bool(original.file) and not original.is_deleted,
            'is_deleted': original.is_deleted,
        }

    def get_read(self, message) -> bool:
        return message.id in self.context.get('read_ids', ())


class MessageCreateSerializer(serializers.Serializer):
    """Entrada de POST /chat/rooms/{id}/messages/: texto (JSON) ou arquivo (multipart), nunca os dois."""

    body = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=MESSAGE_MAX)
    file = serializers.FileField(required=False, allow_null=True)
    reply_to = serializers.IntegerField(required=False, allow_null=True, min_value=1)

    def validate(self, attrs):
        body = (attrs.get('body') or '').strip()
        file = attrs.get('file')

        if bool(body) == bool(file):
            raise serializers.ValidationError('Envie texto ou arquivo, não ambos nem nenhum.')

        attrs['body'] = body or None
        attrs['file'] = file or None

        return attrs
