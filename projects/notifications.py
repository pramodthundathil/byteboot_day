from django.utils import timezone
from .models import ProjectMessage, TaskAuditLog


def get_user_mention(user):
    if not user:
        return ""
    return f"@{user.username}"


def get_task_assignee_mentions(task):
    assignees = task.get_all_assignees()
    if assignees:
        return " ".join([f"@{u.username}" for u in assignees])
    return "@unassigned"


def post_project_chat_message(project, text, sender=None, message_type='TASK_UPDATE', entity_type='TASK', entity_id='', entity_label=''):
    """
    Posts an automated update or user message into the project's dedicated team chat.
    """
    if not sender:
        sender = project.manager or (project.members.first().user if project.members.exists() else None)

    return ProjectMessage.objects.create(
        project=project,
        sender=sender,
        message_type=message_type,
        message_text=text.strip(),
        related_entity_type=entity_type,
        related_entity_id=entity_id,
        related_entity_label=entity_label
    )


def notify_task_created(task, user=None):
    due_str = timezone.localtime(task.due_date).strftime("%d %b %Y, %I:%M %p") if task.due_date else "No deadline"
    mentions = get_task_assignee_mentions(task)
    actor_str = f"@{user.username}" if user else "System"

    text = (
        f"📋 [New Task Created] {task.task_number}: {task.title}\n"
        f"• Status: {task.get_status_display()} | Priority: {task.get_priority_display()} | Est: {task.estimated_hours}h\n"
        f"• Due: {due_str}\n"
        f"• Assigned Team: {mentions}\n"
        f"• Created by: {actor_str}"
    )

    return post_project_chat_message(
        project=task.project,
        text=text,
        sender=user,
        message_type='TASK_UPDATE',
        entity_type='TASK',
        entity_id=task.task_number,
        entity_label=task.title
    )


def notify_task_updated(task, user=None, summary="Task specifications updated"):
    due_str = timezone.localtime(task.due_date).strftime("%d %b %Y, %I:%M %p") if task.due_date else "No deadline"
    mentions = get_task_assignee_mentions(task)
    actor_str = f"@{user.username}" if user else "System"

    text = (
        f"✏️ [Task Updated] {task.task_number}: {task.title}\n"
        f"• Status: {task.get_status_display()} | Priority: {task.get_priority_display()} | Est: {task.estimated_hours}h\n"
        f"• Due: {due_str}\n"
        f"• Assigned Team: {mentions}\n"
        f"• Updated by: {actor_str}\n"
        f"• Changes: {summary}"
    )

    return post_project_chat_message(
        project=task.project,
        text=text,
        sender=user,
        message_type='TASK_UPDATE',
        entity_type='TASK',
        entity_id=task.task_number,
        entity_label=task.title
    )


def notify_task_status_changed(task, old_status, new_status, user=None, comment=""):
    actor_str = f"@{user.username}" if user else "System"
    mentions = get_task_assignee_mentions(task)
    reporter_str = f"@{task.reporter.username}" if task.reporter else "task creator"
    manager_str = f"@{task.project.manager.username}" if task.project.manager else "Project Manager"

    if new_status == 'IN_REVIEW':
        prefix = "📬 [Submit for Review]"
        details = (
            f"• Status: IN_REVIEW (QA / Review Pending)\n"
            f"• Submitted by: {actor_str}\n"
            f"• Review Authority: {reporter_str} or {manager_str}\n"
            f"• Submission Notes: {comment or 'Ready for review and completion.'}"
        )
    elif new_status == 'DONE':
        prefix = "✅ [Task Approved & Done]"
        details = (
            f"• Status: DONE (Completed)\n"
            f"• Approved by: {actor_str}\n"
            f"• Review Feedback: {comment or 'Task verified and approved.'}\n"
            f"• Well done: {mentions}"
        )
    elif new_status == 'REJECTED':
        prefix = "❌ [Task Changes Requested / Rejected]"
        details = (
            f"• Status: REJECTED\n"
            f"• Decision by: {actor_str}\n"
            f"• Feedback/Reason: {comment or 'Revisions requested.'}\n"
            f"• Attention required: {mentions}"
        )
    elif new_status == 'BLOCKED':
        prefix = "⚠️ [Task Blocked]"
        details = (
            f"• Status: BLOCKED\n"
            f"• Reported by: {actor_str}\n"
            f"• Blocker Reason: {comment or 'Work is currently blocked.'}\n"
            f"• Escalated to: {manager_str}"
        )
    else:
        prefix = "🔄 [Task Status Update]"
        details = (
            f"• Status: {task.get_status_display()} (was {old_status})\n"
            f"• Updated by: {actor_str}\n"
            f"• Notes: {comment or 'Status progressed.'}"
        )

    text = f"{prefix} {task.task_number}: {task.title}\n{details}"

    return post_project_chat_message(
        project=task.project,
        text=text,
        sender=user,
        message_type='TASK_UPDATE',
        entity_type='TASK',
        entity_id=task.task_number,
        entity_label=task.title
    )


def notify_work_logged(task, user, hours, description):
    actor_str = f"@{user.username}" if user else "Team Member"
    text = (
        f"⏱️ [Work Logged] {task.task_number}: {task.title}\n"
        f"• Effort: {hours}h logged by {actor_str}\n"
        f"• Work Summary: {description}\n"
        f"• Total Logged: {task.total_actual_hours()}h / {task.estimated_hours}h"
    )

    return post_project_chat_message(
        project=task.project,
        text=text,
        sender=user,
        message_type='TASK_UPDATE',
        entity_type='TASK',
        entity_id=task.task_number,
        entity_label=task.title
    )


def notify_project_updated(project, user=None, summary="Project details updated"):
    actor_str = f"@{user.username}" if user else "Admin"
    manager_str = f"@{project.manager.username}" if project.manager else "Unassigned"

    text = (
        f"📢 [Project Details Updated] {project.code}: {project.name}\n"
        f"• Manager: {manager_str} | Priority: {project.get_priority_display()} | Status: {project.get_status_display()}\n"
        f"• Updated by: {actor_str}\n"
        f"• Summary: {summary}"
    )

    return post_project_chat_message(
        project=project,
        text=text,
        sender=user,
        message_type='PROJECT_UPDATE',
        entity_type='NONE'
    )


def check_and_escalate_overdue_tasks(company=None, project=None):
    """
    Checks for overdue tasks (due_date in the past and status != 'DONE').
    Escalates them by:
    1. Setting is_escalated=True and overdue_escalated_at=now
    2. Posting an alert to the project's team chat mentioning assigned members, manager, and creator
    3. Adding an entry into TaskAuditLog
    Avoids duplicate escalation spam within 24 hours unless due date or status changed.
    """
    from .models import Task

    now = timezone.now()
    qs = Task.objects.filter(
        due_date__isnull=False,
        due_date__lt=now
    ).exclude(status='DONE')

    if company:
        qs = qs.filter(company=company)
    if project:
        qs = qs.filter(project=project)

    escalated_tasks = []

    for task in qs.select_related('project', 'project__manager', 'reporter', 'assigned_to').prefetch_related('assignees'):
        # If already escalated within the last 24 hours, don't spam
        if task.is_escalated and task.overdue_escalated_at:
            time_since_escalation = (now - task.overdue_escalated_at).total_seconds()
            if time_since_escalation < 86400:
                continue

        # Mark task as escalated
        task.is_escalated = True
        task.overdue_escalated_at = now
        task.save(update_fields=['is_escalated', 'overdue_escalated_at'])

        due_local = timezone.localtime(task.due_date)
        due_str = due_local.strftime("%d %b %Y, %I:%M %p")
        
        assignee_mentions = get_task_assignee_mentions(task)
        manager_str = f"@{task.project.manager.username}" if task.project.manager else "Project Manager"
        reporter_str = f"@{task.reporter.username}" if task.reporter else "task creator"

        text = (
            f"🚨 [OVERDUE ESCALATION] Task Deadline Has Elapsed!\n"
            f"• Task: [{task.task_number}] {task.title}\n"
            f"• Current Status: {task.get_status_display()}\n"
            f"• Due Date & Time: {due_str} (OVERDUE)\n"
            f"• Assigned Members: {assignee_mentions}\n"
            f"• Escalated to: {manager_str} and {reporter_str}\n"
            f"• Action Required: Deadline is passed! Please expedite work or submit for review immediately."
        )

        # Post to project chat
        system_user = task.project.manager or task.reporter or (task.company.users.first() if hasattr(task.company, 'users') else None)
        post_project_chat_message(
            project=task.project,
            text=text,
            sender=system_user,
            message_type='ESCALATION',
            entity_type='TASK',
            entity_id=task.task_number,
            entity_label=task.title
        )

        # Record in TaskAuditLog
        TaskAuditLog.objects.create(
            company=task.company,
            task=task,
            user=system_user or task.assigned_to or task.reporter,
            action='STATUS_CHANGE',
            from_status=task.status,
            to_status=task.status,
            comment=f"Automated Overdue Escalation: Deadline ({due_str}) passed. Escalation alert sent to team chat mentioning {assignee_mentions}."
        )

        escalated_tasks.append(task)

    return escalated_tasks
