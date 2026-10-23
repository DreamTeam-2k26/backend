from rest_framework import serializers

from uploader.helpers.files import CONTENT_TYPE_JPG, CONTENT_TYPE_PNG, validate_upload
from uploader.models import Image


class ImageUploadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Image
        fields = ['attachment_key', 'file', 'description', 'uploaded_on', 'url']
        read_only_fields = ['attachment_key', 'uploaded_on', 'url']
        extra_kwargs = {'file': {'write_only': True}}

    def validate_file(self, value):
        return validate_upload(value, [CONTENT_TYPE_JPG, CONTENT_TYPE_PNG], 'Invalid or corrupted image.')


class ImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Image
        fields = ['url', 'description', 'uploaded_on']
        read_only_fields = fields

    def create(self, validated_data):
        raise NotImplementedError('Use ImageUploadSerializer to create images.')
