from django.contrib.auth.models import Permission
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ReadOnlyModelViewSet

from core.permissions import ModelPermissions
from core.serializers import PermissionSerializer


class PermissionViewSet(ReadOnlyModelViewSet):
    """Catálogo de permissões disponíveis no projeto."""

    queryset = Permission.objects.select_related('content_type').order_by(
        'content_type__app_label', 'content_type__model', 'codename'
    )
    serializer_class = PermissionSerializer
    permission_classes = [IsAuthenticated, ModelPermissions]
