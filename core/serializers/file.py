from pathlib import PurePath

from django.conf import settings
from django.urls import reverse
from rest_framework.exceptions import NotFound
from rest_framework.serializers import CharField, ModelSerializer, SerializerMethodField, ValidationError

from core.models import File, Membership
from core.models.file import ALLOWED_FILE_TYPES, FILE_NAME_MAX, file_extension
from uploader.helpers.files import get_content_type


def max_upload_size():
    return settings.PROJECT_FILE_MAX_UPLOAD_MB * 1024 * 1024


class FileListSerializer(ModelSerializer):
    # O caminho no storage nunca sai na API: o conteúdo só é obtido pelo download autorizado.
    uploaded_by_name = CharField(source='uploaded_by.name', read_only=True, default=None)
    download_url = SerializerMethodField()

    class Meta:
        model = File
        fields = (
            'id',
            'project',
            'folder',
            'name',
            'size',
            'mime_type',
            'uploaded_by',
            'uploaded_by_name',
            'uploaded_at',
            'download_url',
        )

    def get_download_url(self, file) -> str:
        return reverse('files-download', kwargs={'pk': file.pk})


class FileRetrieveSerializer(FileListSerializer):
    folder_name = CharField(source='folder.name', read_only=True, default=None)

    class Meta(FileListSerializer.Meta):
        fields = (*FileListSerializer.Meta.fields, 'folder_name')


class FileCreateUpdateSerializer(FileListSerializer):
    """Upload (multipart com file) e renomear/mover (name, folder). Metadados e autoria vêm do servidor."""

    class Meta(FileListSerializer.Meta):
        fields = (*FileListSerializer.Meta.fields, 'file')
        read_only_fields = ('size', 'mime_type', 'uploaded_by', 'uploaded_at')
        extra_kwargs = {
            'file': {'write_only': True},
            'name': {'required': False, 'max_length': FILE_NAME_MAX},
        }

    def validate_project(self, project):
        if self.instance and project != self.instance.project:
            raise ValidationError('Não é possível mover o arquivo para outro projeto.')
        if not Membership.objects.filter(project=project, user=self.context['request'].user).exists():
            raise NotFound('Projeto não encontrado.')
        return project

    def validate_name(self, name):
        if '/' in name or '\\' in name or name in {'.', '..'}:
            raise ValidationError('O nome do arquivo não pode conter barras nem ser "." ou "..".')
        return name

    def validate_file(self, file):
        # Arquivo vazio o próprio FileField do DRF já recusa (allow_empty_file=False).
        if self.instance is not None:
            raise ValidationError('O conteúdo de um arquivo não pode ser trocado; envie um novo arquivo.')
        if file.size > max_upload_size():
            raise ValidationError(f'O arquivo excede o tamanho máximo de {settings.PROJECT_FILE_MAX_UPLOAD_MB} MB.')
        extension = file_extension(file.name)
        if extension not in ALLOWED_FILE_TYPES:
            allowed = ', '.join(sorted(ALLOWED_FILE_TYPES))
            raise ValidationError(f'Tipo de arquivo não permitido. Envie um destes: {allowed}.')
        content_type = get_content_type(file)
        if content_type not in ALLOWED_FILE_TYPES[extension]:
            raise ValidationError(f'O conteúdo do arquivo não corresponde à extensão {extension}.')
        self.detected_mime_type = content_type
        return file

    def validate(self, attrs):
        if self.instance is None and 'file' not in attrs:
            raise ValidationError({'file': 'Envie o arquivo.'})
        project = attrs.get('project') or self.instance.project
        folder = attrs['folder'] if 'folder' in attrs else getattr(self.instance, 'folder', None)
        if folder is not None and folder.project_id != project.pk:
            raise ValidationError({'folder': 'A pasta precisa ser do mesmo projeto do arquivo.'})
        return attrs

    def create(self, validated_data):
        upload = validated_data['file']
        validated_data.setdefault('name', PurePath(upload.name).name[:FILE_NAME_MAX])
        validated_data['size'] = upload.size
        validated_data['mime_type'] = self.detected_mime_type
        return super().create(validated_data)
