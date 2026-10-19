from django.urls import re_path
from projects import consumers

websocket_urlpatterns = [
    re_path(r"^ws/projects/(?P<project_id>\d+)/chat/$", consumers.ProjectChatConsumer.as_asgi()),
    re_path(r"^ws/chat/notifications/$", consumers.GlobalChatNotificationConsumer.as_asgi()),
]
