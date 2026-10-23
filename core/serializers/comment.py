from rest_framework.exceptions import NotFound
from rest_framework.serializers import CharField, ModelSerializer, SerializerMethodField, ValidationError

from core.models import Comment, Membership

COMMENT_BODY_MAX = 5000


class CommentListSerializer(ModelSerializer):
    # author é NULL quando o usuário foi apagado do sistema (o comentário fica como histórico).
    author_name = CharField(source='author.name', read_only=True, default=None)
    author_profile_picture = SerializerMethodField()

    class Meta:
        model = Comment
        fields = ('id', 'task', 'author', 'author_name', 'author_profile_picture', 'body', 'created_at', 'updated_at')

    def get_author_profile_picture(self, comment) -> str | None:
        image = comment.author.profile_picture if comment.author else None
        return image.url if image else None


class CommentRetrieveSerializer(CommentListSerializer):
    class Meta(CommentListSerializer.Meta):
        pass


class CommentCreateUpdateSerializer(ModelSerializer):
    """Cria ou edita comentário. O autor vem do perform_create (token) e nunca do corpo; a tarefa fica fixa."""

    author_name = CharField(source='author.name', read_only=True, default=None)

    class Meta:
        model = Comment
        fields = ('id', 'task', 'author', 'author_name', 'body', 'created_at', 'updated_at')
        read_only_fields = ('author', 'created_at', 'updated_at')
        extra_kwargs = {
            'body': {
                'max_length': COMMENT_BODY_MAX,
                'error_messages': {
                    'blank': 'O comentário não pode ficar vazio.',
                    'max_length': f'O comentário pode ter no máximo {COMMENT_BODY_MAX} caracteres.',
                },
            },
        }

    def validate_task(self, task):
        if self.instance and task != self.instance.task:
            raise ValidationError('Não é possível mover o comentário para outra tarefa.')
        if not Membership.objects.filter(project=task.project_id, user=self.context['request'].user).exists():
            raise NotFound('Tarefa não encontrada.')
        return task
