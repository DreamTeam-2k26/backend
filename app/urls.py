from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.shortcuts import redirect
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import (
    TokenRefreshView,
    TokenVerifyView,
)

from core.views import (
    ChecklistItemViewSet,
    ColumnViewSet,
    CommentViewSet,
    FileViewSet,
    FolderViewSet,
    GoogleLinkView,
    GoogleLoginView,
    GroupTokenObtainPairView,
    GroupViewSet,
    InviteViewSet,
    MeetingViewSet,
    MembershipViewSet,
    PermissionViewSet,
    ProjectViewSet,
    SupportLinkViewSet,
    TaskViewSet,
    UserRegistrationView,
    UserViewSet,
)
from uploader.router import router as uploader_router

router = DefaultRouter()
router.register(r'users', UserViewSet, basename='users')
router.register(r'groups', GroupViewSet, basename='groups')
router.register(r'permissions', PermissionViewSet, basename='permissions')
router.register(r'projects', ProjectViewSet, basename='projects')
router.register(r'memberships', MembershipViewSet, basename='memberships')
router.register(r'invites', InviteViewSet, basename='invites')
router.register(r'columns', ColumnViewSet, basename='columns')
router.register(r'tasks', TaskViewSet, basename='tasks')
router.register(r'checklist-items', ChecklistItemViewSet, basename='checklist-items')
router.register(r'comments', CommentViewSet, basename='comments')
router.register(r'support-links', SupportLinkViewSet, basename='support-links')
router.register(r'meetings', MeetingViewSet, basename='meetings')
router.register(r'folders', FolderViewSet, basename='folders')
router.register(r'files', FileViewSet, basename='files')

urlpatterns = [
    path('', lambda request: redirect('api/', permanent=True)),
    path('admin/', admin.site.urls),
    path('api/', include(router.urls)),
    path('api/media/', include(uploader_router.urls)),
    path('api/chat/', include('chat.urls')),
    path('api/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/doc/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/token/', GroupTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('api/token/verify/', TokenVerifyView.as_view(), name='token_verify'),
    path('api/register/', UserRegistrationView.as_view(), name='user_registration'),
    path('api/auth/google/', GoogleLoginView.as_view(), name='google_login'),
    path('api/auth/google/link/', GoogleLinkView.as_view(), name='google_link'),
]
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
