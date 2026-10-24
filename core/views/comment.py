from django.db.models import OuterRef, Subquery
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from core.models import Comment, Membership
from core.permissions import CommentPermission
from core.serializers import CommentCreateUpdateSerializer, CommentListSerializer, CommentRetrieveSerializer


@extend_schema_view(
    list=extend_schema(summary='Comentários das tarefas, do mais antigo ao mais novo (filtre com ?task=<id>)'),
    create=extend_schema(summary='Comentar em uma tarefa (qualquer participante; autor vem do token)'),
    retrieve=extend_schema(summary='Detalhes do comentário'),
    update=extend_schema(summary='Editar comentário (somente o autor)'),
    partial_update=extend_schema(summary='Editar comentário (somente o autor)'),
    destroy=extend_schema(summary='Excluir comentário (autor, ou dono/administrador do projeto)'),
)
class CommentViewSet(ModelViewSet):
    """Comentários das tarefas dos projetos do usuário autenticado.

    Qualquer participante lê e comenta; editar é só do autor e excluir é do autor ou de dono/admin
    (CommentPermission). Comentário de tarefa de projeto alheio responde 404.
    """

    queryset = Comment.objects.none()  # só para o schema/filtros descobrirem o model; o real é get_queryset
    permission_classes = [IsAuthenticated, CommentPermission]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ('task', 'author')
    lookup_value_regex = r'\d+'

    def get_queryset(self):
        user = self.request.user
        my_projects = Membership.objects.filter(user=user).values('project')
        my_role = Membership.objects.filter(project=OuterRef('task__project'), user=user).values('role')[:1]
        return (
            Comment.objects
            .filter(task__project__in=my_projects)
            .annotate(my_role=Subquery(my_role))
            .select_related('author__profile_picture')
            .order_by('task_id', 'created_at', 'id')
        )

    def get_serializer_class(self):
        if self.action == 'list':
            return CommentListSerializer
        if self.action in {'create', 'update', 'partial_update'}:
            return CommentCreateUpdateSerializer
        return CommentRetrieveSerializer

    def perform_create(self, serializer):
        serializer.save(author=self.request.user)
