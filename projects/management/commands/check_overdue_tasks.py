from django.core.management.base import BaseCommand
from projects.notifications import check_and_escalate_overdue_tasks


class Command(BaseCommand):
    help = "Checks for overdue tasks and posts escalation alerts to project team chats with member mentions."

    def handle(self, *args, **options):
        self.stdout.write("Checking for overdue tasks across all companies and projects...")
        escalated = check_and_escalate_overdue_tasks()
        self.stdout.write(self.style.SUCCESS(f"Successfully processed overdue checks. Escalated {len(escalated)} task(s)."))
