from rest_framework import mixins, parsers, viewsets
from rest_framework.permissions import IsAuthenticated

from core.permissions import ModelPermissions
from uploader.models import Document, Image
from uploader.serializers import DocumentUploadSerializer, ImageUploadSerializer


class CreateViewSet(mixins.CreateModelMixin, viewsets.GenericViewSet):
    """Só permite envio: o attachment_key devolvido é o comprovante de posse do arquivo."""

    parser_classes = [parsers.FormParser, parsers.MultiPartParser]
    permission_classes = [IsAuthenticated, ModelPermissions]

    def perform_create(self, serializer):
        serializer.save(uploaded_by=self.request.user)


class DocumentUploadViewSet(CreateViewSet):
    queryset = Document.objects.all()
    serializer_class = DocumentUploadSerializer


class ImageUploadViewSet(CreateViewSet):
    queryset = Image.objects.all()
    serializer_class = ImageUploadSerializer
