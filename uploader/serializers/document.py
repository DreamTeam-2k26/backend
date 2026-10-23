from rest_framework import serializers

from uploader.helpers.files import CONTENT_TYPE_PDF, validate_upload
from uploader.models import Document


class DocumentUploadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Document
        fields = ['attachment_key', 'file', 'description', 'uploaded_on']
        read_only_fields = ['attachment_key', 'uploaded_on']
        extra_kwargs = {'file': {'write_only': True}}

    def validate_file(self, value):
        return validate_upload(value, [CONTENT_TYPE_PDF], 'Invalid or corrupted document.')


class DocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Document
        fields = ['url', 'description', 'uploaded_on', 'attachment_key', 'public_id']
        read_only_fields = fields

    def create(self, validated_data):
        raise NotImplementedError('Use DocumentUploadSerializer to create document files.')
