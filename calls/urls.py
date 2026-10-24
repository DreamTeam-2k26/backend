from rest_framework.routers import SimpleRouter

from .views import CallViewSet

router = SimpleRouter()
router.register('', CallViewSet, basename='calls')

urlpatterns = router.urls
