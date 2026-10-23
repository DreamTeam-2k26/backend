from django.db.models import OuterRef, Subquery
from django.db.models.functions import Lower
from django.http import FileResponse
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from core.models import File, Membership
from core.permissions import FilePermission
from core.serializers import FileCreateUpdateSerializer, FileListSerializer, FileRetrieveSerializer

# Mesmos cuidados do download de anexos do chat: o navegador nunca interpreta o arquivo enviado.
DOWNLOAD_SECURITY_HEADERS = {
    'X-Content-Type-Options': 'nosniff',
    'Content-Security-Policy': "default-src 'none'; sandbox",
    'Cache-Control': 'private, max-age=3600',
}


@extend_schema_view(
    list=extend_schema(
        summary='Arquivos: de uma pasta com ?folder=<id>, ou da raiz com ?project=<id>&folder__isnull=true'
    ),
    create=extend_schema(
        summary='Enviar arquivo (multipart: project, file, folder e name opcionais; qualquer participante)'
    ),
    retrieve=extend_schema(summary='Metadados do arquivo'),
    update=extend_schema(summary='Renomear/mover arquivo (qualquer participante)'),
    partial_update=extend_schema(summary='Renomear (name) ou mover (folder; null = raiz) o arquivo'),
    destroy=extend_schema(summary='Excluir arquivo, inclusive do storage (qualquer participante)'),
)
class FileViewSet(ModelViewSet):
    """Arquivos dos projetos do usuário autenticado.

    Qualquer participante envia, baixa, renomeia, move e exclui (FilePermission). Arquivo de projeto alheio
    responde 404, inclusive no download. O conteúdo só sai pelo download autenticado, nunca por URL do storage.
    """

    queryset = File.objects.none()  # só para o schema/filtros descobrirem o model; o real é get_queryset
    permission_classes = [IsAuthenticated, FilePermission]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = {'project': ['exact'], 'folder': ['exact', 'isnull']}
    lookup_value_regex = r'\d+'

    def get_queryset(self):
        user = self.request.user
        my_projects = Membership.objects.filter(user=user).values('project')
        my_role = Membership.objects.filter(project=OuterRef('project'), user=user).values('role')[:1]
        return (
            File.objects
            .filter(project__in=my_projects)
            .annotate(my_role=Subquery(my_role))
            .select_related('folder', 'uploaded_by')
            .order_by(Lower('name'), 'id')
        )

    def get_serializer_class(self):
        if self.action == 'list':
            return FileListSerializer
        if self.action in {'create', 'update', 'partial_update'}:
            return FileCreateUpdateSerializer
        return FileRetrieveSerializer

    def perform_create(self, serializer):
        serializer.save(uploaded_by=self.request.user)

    @extend_schema(
        summary='Baixar arquivo (somente participantes do projeto)',
        responses={200: OpenApiResponse(OpenApiTypes.BINARY, description='Conteúdo do arquivo (attachment).')},
    )
    @action(detail=True, methods=['get'])
    def download(self, request, pk=None):
        file = self.get_object()
        try:
            stream = file.file.open('rb')
        except (FileNotFoundError, OSError) as error:
            raise NotFound('Arquivo não encontrado.') from error

        response = FileResponse(stream, as_attachment=True, filename=file.name, content_type=file.mime_type)
        for header, value in DOWNLOAD_SECURITY_HEADERS.items():
            response[header] = value
        return response
