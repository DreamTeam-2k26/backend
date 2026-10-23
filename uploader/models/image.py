import uuid

from django.conf import settings
from django.db import models

from uploader.helpers.files import get_extension


def image_file_path(image, _) -> str:
    return f'images/{image.public_id}{get_extension(image.file)}'


class Image(models.Model):
    attachment_key = models.UUIDField(
        max_length=255,
        default=uuid.uuid4,
        unique=True,
        help_text=('Used to attach the image to another object. Cannot be used to retrieve the image file.'),
    )
    public_id = models.UUIDField(
        max_length=255,
        default=uuid.uuid4,
        unique=True,
        help_text=(
            'Used to retrieve the image itself. Should not be readable until the image is attached to another object.'
        ),
    )
    file = models.ImageField(upload_to=image_file_path)
    description = models.CharField(max_length=255, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='images',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    uploaded_on = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f'{self.description} - {self.attachment_key}'

    @property
    def url(self) -> str:
        return self.file.url  # pylint: disable=no-member
