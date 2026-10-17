import json
import logging
from django.utils import timezone
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

logger = logging.getLogger(__name__)


def serialize_project_message(msg):
    """
    Format a ProjectMessage instance into a clean JSON-serializable dictionary.
    """
    sender_name = "System"
    sender_username = "system"
    sender_id = None
    if msg.sender:
        sender_id = msg.sender_id
        sender_username = msg.sender.username
        try:
            emp = getattr(msg.sender, 'employee_profile', None)
            if emp and getattr(emp, 'full_name', None):
                sender_name = emp.full_name
            elif msg.sender.get_full_name():
                sender_name = msg.sender.get_full_name()
            else:
                sender_name = msg.sender.username
        except Exception:
            sender_name = msg.sender.username

    local_time = timezone.localtime(msg.created_at) if msg.created_at else timezone.localtime(timezone.now())

    return {
        "id": msg.id,
        "project_id": msg.project_id,
        "sender_id": sender_id,
        "sender_username": sender_username,
        "sender_name": sender_name,
        "message_type": msg.message_type,
        "message_text": msg.message_text,
        "related_entity_type": msg.related_entity_type or "NONE",
        "related_entity_id": msg.related_entity_id or "",
        "related_entity_label": msg.related_entity_label or "",
        "created_at": local_time.strftime("%H:%M"),
    }


def broadcast_project_message(msg):
    """
    Broadcast a ProjectMessage payload to the project's WebSocket channel layer group.
    """
    channel_layer = get_channel_layer()
    if not channel_layer:
        return
    payload = serialize_project_message(msg)
    async_to_sync(channel_layer.group_send)(
        f"project_chat_{msg.project_id}",
        {
            "type": "chat_message_broadcast",
            "message": payload,
        }
    )


class ProjectChatConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for real-time project messaging.
    """
    async def connect(self):
        self.user = self.scope.get("user")
        if not self.user or not self.user.is_authenticated:
            await self.close(code=4001)
            return

        self.project_id = self.scope["url_route"]["kwargs"].get("project_id")
        try:
            self.project_id = int(self.project_id)
        except (ValueError, TypeError):
            await self.close(code=4002)
            return

        # Verify access permission
        access_info = await self.check_project_access(self.user, self.project_id)
        if not access_info.get("has_access"):
            await self.close(code=4003)
            return

        self.project_code = access_info.get("project_code", "")
        self.can_view_tasks = access_info.get("can_view_tasks", False)
        self.can_view_invoices = access_info.get("can_view_invoices", False)
        self.can_view_leads = access_info.get("can_view_leads", False)

        self.room_group_name = f"project_chat_{self.project_id}"
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )

        await self.accept()

        # Send initial connected handshake confirmation
        await self.send(text_data=json.dumps({
            "type": "connection_established",
            "project_id": self.project_id,
            "project_code": self.project_code,
            "user_id": self.user.id,
            "username": self.user.username,
        }))

    async def disconnect(self, close_code):
        if hasattr(self, "room_group_name"):
            await self.channel_layer.group_discard(
                self.room_group_name,
                self.channel_name
            )

    async def receive(self, text_data=None, bytes_data=None):
        if not text_data:
            return

        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            return

        action = data.get("action", "send_message")
        if action == "ping":
            await self.send(text_data=json.dumps({"type": "pong"}))
            return

        message_text = data.get("message_text", "").strip()
        if not message_text:
            return

        entity_type = data.get("entity_type", "NONE")
        entity_id = data.get("entity_id", "").strip()
        entity_label = data.get("entity_label", "").strip()

        # Enforce permission checks matching views.py
        if entity_type == 'TASK' and not self.can_view_tasks:
            entity_type = 'NONE'
            entity_id = ''
            entity_label = ''
        elif entity_type == 'INVOICE' and not self.can_view_invoices:
            entity_type = 'NONE'
            entity_id = ''
            entity_label = ''
        elif entity_type == 'LEAD' and not self.can_view_leads:
            entity_type = 'NONE'
            entity_id = ''
            entity_label = ''
        elif entity_type == 'NONE':
            entity_id = ''
            entity_label = ''

        # Create message in DB; post_save signal broadcasts to room
        await self.save_message(
            project_id=self.project_id,
            sender=self.user,
            message_text=message_text,
            entity_type=entity_type,
            entity_id=entity_id,
            entity_label=entity_label
        )

    async def chat_message_broadcast(self, event):
        # Forward the broadcast message to WebSocket client
        await self.send(text_data=json.dumps({
            "type": "chat_message",
            "message": event["message"]
        }))

    @database_sync_to_async
    def check_project_access(self, user, project_id):
        from projects.models import Project
        from tenants.models import CompanyMembership
        from projects.views import is_project_admin, can_access_project

        try:
            project = Project.objects.select_related('company', 'manager').get(id=project_id)
        except Project.DoesNotExist:
            return {"has_access": False}

        membership = CompanyMembership.objects.filter(
            user=user, company=project.company, is_active=True
        ).first()

        admin_user = is_project_admin(user, membership)
        has_access = can_access_project(user, membership, project)

        can_view_invoices = admin_user or (membership and membership.has_permission('invoicing', 'view'))
        can_view_leads = admin_user or (membership and membership.has_permission('crm', 'view'))
        can_view_tasks = admin_user or has_access

        return {
            "has_access": bool(has_access),
            "project_code": project.code,
            "can_view_tasks": bool(can_view_tasks),
            "can_view_invoices": bool(can_view_invoices),
            "can_view_leads": bool(can_view_leads),
        }

    @database_sync_to_async
    def save_message(self, project_id, sender, message_text, entity_type, entity_id, entity_label):
        from projects.models import ProjectMessage
        return ProjectMessage.objects.create(
            project_id=project_id,
            sender=sender,
            message_type='CHAT',
            message_text=message_text,
            related_entity_type=entity_type,
            related_entity_id=entity_id,
            related_entity_label=entity_label
        )
