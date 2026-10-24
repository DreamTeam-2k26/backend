import os

os.environ.setdefault(
    'DJANGO_SETTINGS_MODULE',
    'app.settings',
)

from django.core.asgi import get_asgi_application

django_asgi_application = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402

from core.middleware import JWTAuthMiddlewareStack  # noqa: E402
from calls.routing import websocket_urlpatterns as calls_websocket_urlpatterns  # noqa: E402
from chat.routing import websocket_urlpatterns as chat_websocket_urlpatterns  # noqa: E402
from core.routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter({
    'http': django_asgi_application,
    'websocket': JWTAuthMiddlewareStack(
        URLRouter([*websocket_urlpatterns, *chat_websocket_urlpatterns, *calls_websocket_urlpatterns])
    ),
})
