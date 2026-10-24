from django.urls import re_path

from .consumers import CallNotifyConsumer, CallRoomConsumer

websocket_urlpatterns = [
    re_path(r'^ws/calls/$', CallNotifyConsumer.as_asgi()),
    re_path(r'^ws/calls/(?P<call_id>\d+)/$', CallRoomConsumer.as_asgi()),
]
