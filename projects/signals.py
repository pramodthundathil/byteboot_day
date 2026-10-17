import logging
from django.db.models.signals import post_save
from django.dispatch import receiver
from projects.models import ProjectMessage

logger = logging.getLogger(__name__)


@receiver(post_save, sender=ProjectMessage)
def project_message_created(sender, instance, created, raw=False, **kwargs):
    """
    Whenever a ProjectMessage is created (via WebSocket, form POST, or system notification),
    broadcast it immediately to the project's WebSocket channel layer group.
    """
    if raw or not created:
        return

    try:
        from projects.consumers import broadcast_project_message
        broadcast_project_message(instance)
    except Exception as e:
        logger.debug("Project message broadcast skipped or failed: %s", e)
