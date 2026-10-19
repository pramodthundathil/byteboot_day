from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages
from decimal import Decimal
from datetime import datetime
from django.utils import timezone
from django.utils.dateparse import parse_datetime, parse_date
from django.db.models import Q, Count, Max
from django.db.models.functions import Coalesce
from projects.models import Project, ProjectMember, Task, WorkLog, ProjectMessage, TaskAuditLog, ProjectMessageRead
from projects.consumers import format_date_label, serialize_project_message
from projects.notifications import (
    notify_task_created,
    notify_task_updated,
    notify_task_status_changed,
    notify_work_logged,
    notify_project_updated,
    check_and_escalate_overdue_tasks,
)
from crm.models import Customer, Lead
from invoicing.models import Invoice
from tenants.decorators import require_permission
from django.http import JsonResponse

def parse_form_datetime(val):
    if not val:
        return None
    if isinstance(val, datetime):
        if timezone.is_naive(val):
            return timezone.make_aware(val, timezone.get_current_timezone())
        return val
    val_str = str(val).strip()
    dt = parse_datetime(val_str)
    if dt is not None:
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_current_timezone())
        return dt
    d = parse_date(val_str)
    if d is not None:
        naive_dt = datetime.combine(d, datetime.min.time())
        return timezone.make_aware(naive_dt, timezone.get_current_timezone())
    return None


def is_project_admin(user, membership):
    if user.is_superuser:
        return True
    if membership and (
        membership.role in ('COMPANY_ADMIN', 'PLATFORM_ADMIN') or
        membership.has_permission('projects', 'assign') or
        membership.has_permission('projects', 'approve')
    ):
        return True
    return False

def can_access_project(user, membership, project):
    if is_project_admin(user, membership):
        return True
    return (project.manager_id == user.id) or project.members.filter(user=user).exists()

def can_edit_project(user, membership, project):
    if is_project_admin(user, membership):
        return True
    if project.manager_id == user.id:
        return True
    if project.members.filter(user=user, role='MANAGER').exists():
        return True
    if membership and membership.has_permission('projects', 'edit') and project.members.filter(user=user).exists():
        return True
    return False

def can_user_approve_or_complete_task(user, membership, task):
    if user.is_superuser:
        return True
    if membership and (
        membership.role in ('COMPANY_ADMIN', 'PLATFORM_ADMIN') or
        membership.has_permission('projects', 'approve')
    ):
        return True
    if task.project.manager_id == user.id:
        return True
    if task.project.members.filter(user=user, role='MANAGER').exists():
        return True
    if task.reporter_id == user.id:
        return True
    return False


@login_required
@require_permission('projects', 'view')
def project_list(request):
    company = request.company
    membership = getattr(request, 'membership', None)
    admin_user = is_project_admin(request.user, membership)

    if admin_user:
        projects = Project.objects.filter(company=company)
    else:
        projects = Project.objects.filter(company=company).filter(
            Q(members__user=request.user) | Q(manager=request.user)
        ).distinct()

    projects = projects.select_related('customer', 'manager').prefetch_related(
        'members__user',
        'members__user__employee_profile'
    )

    can_create = admin_user or (membership and membership.has_permission('projects', 'create'))

    return render(request, 'projects/project_list.html', {
        'projects': projects,
        'is_admin': admin_user,
        'can_create': can_create,
        'active_tab': 'projects',
        'active_sub': 'projects'
    })


@login_required
@require_permission('projects', 'create')
def project_create(request):
    company = request.company
    membership = getattr(request, 'membership', None)
    admin_user = is_project_admin(request.user, membership)
    customers = Customer.objects.filter(company=company)
    assignable_users = User.objects.filter(
        memberships__company=company,
        memberships__is_active=True
    ).select_related('employee_profile').distinct().order_by('first_name', 'last_name', 'username')

    if request.method == 'POST':
        count = Project.objects.filter(company=company).count() + 1
        code = f"PRJ-{100 + count}"

        cust_id = request.POST.get('customer_id')
        customer = Customer.objects.filter(id=cust_id, company=company).first() if cust_id else None

        mgr_id = request.POST.get('manager_id')
        manager_user = User.objects.filter(id=mgr_id, memberships__company=company).first() if mgr_id else request.user

        end_d = request.POST.get('target_end_date')

        project = Project.objects.create(
            company=company,
            code=code,
            name=request.POST.get('name'),
            customer=customer,
            description=request.POST.get('description', ''),
            start_date=request.POST.get('start_date', timezone.now().date()),
            target_end_date=end_d if end_d else None,
            status=request.POST.get('status', 'IN_PROGRESS'),
            priority=request.POST.get('priority', 'MEDIUM'),
            manager=manager_user,
            budget=Decimal(request.POST.get('budget', '100000.00') or '0.00'),
            estimated_hours=Decimal(request.POST.get('estimated_hours', '160.00') or '0.00')
        )

        member_ids = request.POST.getlist('member_ids')
        if not member_ids:
            member_ids = [str(manager_user.id)]
        elif str(manager_user.id) not in member_ids:
            member_ids.append(str(manager_user.id))

        for u_id in member_ids:
            user_obj = User.objects.filter(id=u_id, memberships__company=company).first()
            if not user_obj:
                continue
            role = request.POST.get(f'role_{u_id}')
            if not role:
                role = 'MANAGER' if user_obj.id == manager_user.id else 'DEVELOPER'
            if user_obj.id == manager_user.id:
                role = 'MANAGER'
            ProjectMember.objects.create(project=project, user=user_obj, role=role)

        messages.success(request, f"Project {project.code} ({project.name}) created successfully.")
        return redirect('projects:project_detail', project_id=project.id)

    return render(request, 'projects/project_form.html', {
        'customers': customers,
        'assignable_users': assignable_users,
        'status_choices': Project.STATUS_CHOICES,
        'priority_choices': Project.PRIORITY_CHOICES,
        'role_choices': ProjectMember.ROLE_CHOICES,
        'is_admin': admin_user,
        'active_tab': 'projects',
        'active_sub': 'projects'
    })


@login_required
@require_permission('projects', 'view')
def project_detail(request, project_id):
    company = request.company
    project = get_object_or_404(Project, id=project_id, company=company)
    membership = getattr(request, 'membership', None)
    admin_user = is_project_admin(request.user, membership)

    if not can_access_project(request.user, membership, project):
        messages.error(request, f"Access Denied: You are not an assigned team member of project {project.code}.")
        return redirect('projects:project_list')

    can_edit = can_edit_project(request.user, membership, project)

    tasks = project.tasks.all()
    if not admin_user:
        active_users_count = User.objects.filter(
            memberships__company=company,
            memberships__is_active=True
        ).distinct().count()

        tasks = tasks.annotate(num_assignees=Count('assignees')).filter(
            Q(assigned_to=request.user) | 
            Q(assignees=request.user) |
            Q(reporter=request.user) |
            (Q(num_assignees__gte=active_users_count) if active_users_count > 0 else Q())
        ).distinct()

    check_and_escalate_overdue_tasks(company=company, project=project)
    messages_list = project.messages.all()

    return render(request, 'projects/project_detail.html', {
        'project': project,
        'tasks': tasks,
        'is_admin': admin_user,
        'can_edit': can_edit,
        'messages_list': messages_list,
        'members': project.members.select_related('user', 'user__employee_profile').all(),
        'active_tab': 'projects',
        'active_sub': 'projects'
    })


@login_required
@require_permission('projects', 'edit')
def project_edit(request, project_id):
    company = request.company
    project = get_object_or_404(Project, id=project_id, company=company)
    membership = getattr(request, 'membership', None)
    admin_user = is_project_admin(request.user, membership)

    if not can_edit_project(request.user, membership, project):
        messages.error(request, f"Access Denied: You do not have permission to edit project {project.code}.")
        return redirect('projects:project_detail', project_id=project.id)

    customers = Customer.objects.filter(company=company)
    assignable_users = User.objects.filter(
        memberships__company=company,
        memberships__is_active=True
    ).select_related('employee_profile').distinct().order_by('first_name', 'last_name', 'username')

    if request.method == 'POST':
        project.name = request.POST.get('name', project.name)

        cust_id = request.POST.get('customer_id')
        project.customer = Customer.objects.filter(id=cust_id, company=company).first() if cust_id else None

        mgr_id = request.POST.get('manager_id')
        if mgr_id:
            mgr_user = User.objects.filter(id=mgr_id, memberships__company=company).first()
            if mgr_user:
                project.manager = mgr_user

        start_d = request.POST.get('start_date')
        if start_d:
            project.start_date = start_d

        end_d = request.POST.get('target_end_date')
        project.target_end_date = end_d if end_d else None

        project.status = request.POST.get('status', project.status)
        project.priority = request.POST.get('priority', project.priority)

        budget = request.POST.get('budget')
        if budget:
            try:
                project.budget = Decimal(budget)
            except Exception:
                pass

        est_hours = request.POST.get('estimated_hours')
        if est_hours:
            try:
                project.estimated_hours = Decimal(est_hours)
            except Exception:
                pass

        project.description = request.POST.get('description', '')
        project.save()

        # Update Project Members
        member_ids = request.POST.getlist('member_ids')

        # Always ensure project manager is included in assigned members
        if project.manager and str(project.manager.id) not in member_ids:
            member_ids.append(str(project.manager.id))

        # Remove members who are unselected
        ProjectMember.objects.filter(project=project).exclude(user_id__in=member_ids).delete()

        # Create or update selected members with designated roles
        for u_id in member_ids:
            user_obj = User.objects.filter(id=u_id, memberships__company=company).first()
            if not user_obj:
                continue

            role = request.POST.get(f'role_{u_id}')
            if not role:
                role = 'MANAGER' if (project.manager and user_obj.id == project.manager.id) else 'DEVELOPER'
            if project.manager and user_obj.id == project.manager.id:
                role = 'MANAGER'

            ProjectMember.objects.update_or_create(
                project=project,
                user=user_obj,
                defaults={'role': role}
            )

        notify_project_updated(project, user=request.user, summary=f"Updated project settings and {len(member_ids)} team member(s).")
        messages.success(request, f"Project {project.code} ({project.name}) and team members updated successfully.")
        return redirect('projects:project_detail', project_id=project.id)

    current_members = {m.user_id: m.role for m in project.members.all()}
    if project.manager and project.manager_id not in current_members:
        current_members[project.manager_id] = 'MANAGER'

    return render(request, 'projects/project_form_edit.html', {
        'project': project,
        'customers': customers,
        'assignable_users': assignable_users,
        'current_members': current_members,
        'status_choices': Project.STATUS_CHOICES,
        'priority_choices': Project.PRIORITY_CHOICES,
        'role_choices': ProjectMember.ROLE_CHOICES,
        'is_admin': admin_user,
        'active_tab': 'projects',
        'active_sub': 'projects'
    })


@login_required
@require_permission('projects', 'view')
def task_list(request):
    company = request.company

    is_admin = (
        request.user.is_superuser or
        (hasattr(request, 'membership') and (
            request.membership.role in ('COMPANY_ADMIN', 'PLATFORM_ADMIN') or
            request.membership.has_permission('projects', 'assign') or
            request.membership.has_permission('projects', 'approve')
        ))
    )

    tasks = Task.objects.filter(company=company)
    if not is_admin:
        active_users_count = User.objects.filter(
            memberships__company=company,
            memberships__is_active=True
        ).distinct().count()

        tasks = tasks.annotate(num_assignees=Count('assignees')).filter(
            Q(assigned_to=request.user) | 
            Q(assignees=request.user) |
            Q(reporter=request.user) |
            (Q(num_assignees__gte=active_users_count) if active_users_count > 0 else Q())
        ).distinct()

    status_filter = request.GET.get('status', '')
    if status_filter:
        tasks = tasks.filter(status=status_filter)

    check_and_escalate_overdue_tasks(company=company)

    return render(request, 'projects/task_list.html', {
        'tasks': tasks,
        'status_filter': status_filter,
        'is_admin': is_admin,
        'active_tab': 'projects',
        'active_sub': 'tasks'
    })


@login_required
@require_permission('projects', 'create')
def task_create(request):
    company = request.company
    membership = getattr(request, 'membership', None)
    is_admin = is_project_admin(request.user, membership)

    projects = Project.objects.filter(company=company)
    if not is_admin:
        projects = projects.filter(Q(members__user=request.user) | Q(manager=request.user)).distinct()

    assignable_users = User.objects.filter(
        memberships__company=company,
        memberships__is_active=True
    ).select_related('employee_profile').distinct().order_by('first_name', 'last_name', 'username')

    selected_project_id = request.GET.get('project_id') or request.POST.get('project_id')

    if request.method == 'POST':
        prj_id = request.POST.get('project_id')
        project = get_object_or_404(projects, id=prj_id)

        count = Task.objects.filter(company=company).count() + 1
        t_num = f"TSK-{500 + count}"

        assign_all = request.POST.get('assign_all_employees') == 'true' or 'assign_all' in request.POST
        assigned_to_ids = request.POST.getlist('assigned_to_ids')

        if assign_all:
            selected_users = list(assignable_users)
        elif assigned_to_ids:
            selected_users = list(User.objects.filter(id__in=assigned_to_ids, memberships__company=company).distinct())
        else:
            single_id = request.POST.get('assigned_to_id')
            if single_id:
                selected_users = list(User.objects.filter(id=single_id, memberships__company=company))
            else:
                selected_users = [request.user]

        assigned_primary = selected_users[0] if selected_users else request.user

        start_d = parse_form_datetime(request.POST.get('start_date'))
        due_d = parse_form_datetime(request.POST.get('due_date'))
        est_hrs_raw = request.POST.get('estimated_hours')
        try:
            est_hours = Decimal(est_hrs_raw) if est_hrs_raw else Decimal('8.00')
        except Exception:
            est_hours = Decimal('8.00')

        task = Task.objects.create(
            company=company,
            project=project,
            task_number=t_num,
            title=request.POST.get('title'),
            description=request.POST.get('description', ''),
            assigned_to=assigned_primary,
            reporter=request.user,
            status=request.POST.get('status', 'TO_DO'),
            priority=request.POST.get('priority', 'MEDIUM'),
            estimated_hours=est_hours,
            start_date=start_d,
            due_date=due_d
        )
        task.assignees.set(selected_users)

        TaskAuditLog.objects.create(
            company=company,
            task=task,
            user=request.user,
            action='CREATED',
            to_status=task.status,
            comment=f"Task {task.task_number} created with initial status {task.get_status_display()}."
        )

        notify_task_created(task, user=request.user)

        messages.success(request, f"Task {task.task_number} created and assigned to {len(selected_users)} employee(s)/user(s).")
        return redirect('projects:task_detail', task_id=task.id)

    return render(request, 'projects/task_form.html', {
        'projects': projects,
        'assignable_users': assignable_users,
        'selected_project_id': selected_project_id,
        'active_tab': 'projects',
        'active_sub': 'tasks'
    })


@login_required
@require_permission('projects', 'edit')
def task_edit(request, task_id):
    company = request.company
    task = get_object_or_404(Task, id=task_id, company=company)
    membership = getattr(request, 'membership', None)
    is_admin = is_project_admin(request.user, membership)

    projects = Project.objects.filter(company=company)
    if not is_admin:
        projects = projects.filter(Q(members__user=request.user) | Q(manager=request.user)).distinct()

    assignable_users = User.objects.filter(
        memberships__company=company,
        memberships__is_active=True
    ).select_related('employee_profile').distinct().order_by('first_name', 'last_name', 'username')

    if request.method == 'POST':
        prj_id = request.POST.get('project_id')
        if prj_id:
            task.project = get_object_or_404(projects, id=prj_id)

        task.title = request.POST.get('title', task.title)
        task.description = request.POST.get('description', '')
        
        assign_all = request.POST.get('assign_all_employees') == 'true' or 'assign_all' in request.POST
        assigned_to_ids = request.POST.getlist('assigned_to_ids')

        if assign_all:
            selected_users = list(assignable_users)
        elif assigned_to_ids:
            selected_users = list(User.objects.filter(id__in=assigned_to_ids, memberships__company=company).distinct())
        else:
            single_id = request.POST.get('assigned_to_id')
            if single_id:
                selected_users = list(User.objects.filter(id=single_id, memberships__company=company))
            else:
                selected_users = []

        task.assignees.set(selected_users)
        task.assigned_to = selected_users[0] if selected_users else None

        task.status = request.POST.get('status', task.status)
        if task.status == 'DONE' and not task.completed_at:
            task.completed_at = timezone.now()

        task.priority = request.POST.get('priority', task.priority)
        
        est_hrs = request.POST.get('estimated_hours')
        if est_hrs:
            try:
                task.estimated_hours = Decimal(est_hrs)
            except Exception:
                pass
            
        start_d_raw = request.POST.get('start_date')
        if start_d_raw is not None:
            task.start_date = parse_form_datetime(start_d_raw)

        due_d_raw = request.POST.get('due_date')
        if due_d_raw is not None:
            task.due_date = parse_form_datetime(due_d_raw)

        task.save()
        TaskAuditLog.objects.create(
            company=company,
            task=task,
            user=request.user,
            action='UPDATED',
            to_status=task.status,
            comment="Task specifications or assignments updated."
        )
        notify_task_updated(task, user=request.user, summary=f"Updated task specifications and assigned {len(selected_users)} employee(s).")
        messages.success(request, f"Task {task.task_number} updated successfully. Assigned to {len(selected_users)} employee(s)/user(s).")
        return redirect('projects:task_detail', task_id=task.id)

    current_assignee_ids = list(task.assignees.values_list('id', flat=True))
    if task.assigned_to_id and task.assigned_to_id not in current_assignee_ids:
        current_assignee_ids.append(task.assigned_to_id)

    return render(request, 'projects/task_form_edit.html', {
        'task': task,
        'projects': projects,
        'assignable_users': assignable_users,
        'current_assignee_ids': current_assignee_ids,
        'active_tab': 'projects',
        'active_sub': 'tasks'
    })


@login_required
@require_permission('projects', 'view')
def task_detail(request, task_id):
    company = request.company
    task = get_object_or_404(Task, id=task_id, company=company)
    membership = getattr(request, 'membership', None)

    can_complete = can_user_approve_or_complete_task(request.user, membership, task)
    is_assigned = (task.assigned_to_id == request.user.id) or task.assignees.filter(id=request.user.id).exists()
    can_change_status = can_complete or is_assigned or request.user.is_superuser or (
        membership and membership.has_permission('projects', 'edit')
    )

    if request.method == 'POST':
        new_status = request.POST.get('status')
        if new_status:
            valid_statuses = dict(Task.STATUS_CHOICES)
            if new_status not in valid_statuses:
                messages.error(request, "Invalid status choice selected.")
                return redirect('projects:task_detail', task_id=task.id)

            if not can_change_status:
                messages.error(request, "Access Denied: You do not have permission to change this task's status.")
                return redirect('projects:task_detail', task_id=task.id)

            # Restrict DONE / Completed strictly to Admin, Project Manager, or Task Creator
            if new_status == 'DONE' and not can_complete:
                messages.error(request, f"Access Denied: Only Administrators or the task creator (@{task.reporter.username if task.reporter else 'reporter'}) can approve and mark this task as Done.")
                return redirect('projects:task_detail', task_id=task.id)

            old_status = task.status
            comment = (request.POST.get('status_comment') or request.POST.get('review_comment') or '').strip()
            if old_status != new_status:
                task.status = new_status
                if new_status == 'DONE':
                    task.completed_at = timezone.now()
                    task.is_escalated = False
                    task.overdue_escalated_at = None
                elif old_status == 'DONE' and new_status != 'DONE':
                    task.completed_at = None
                task.save()

                if new_status == 'IN_REVIEW':
                    action = 'SUBMIT_REVIEW'
                    if not comment:
                        comment = "Submitted task for QA & Management review."
                elif new_status == 'DONE':
                    action = 'APPROVED_DONE'
                    if not comment:
                        comment = "Task approved and marked as Completed."
                elif new_status == 'REJECTED':
                    action = 'REJECTED'
                    if not comment:
                        comment = "Task rejected / changes requested."
                else:
                    action = 'STATUS_CHANGE'

                TaskAuditLog.objects.create(
                    company=company,
                    task=task,
                    user=request.user,
                    action=action,
                    from_status=old_status,
                    to_status=new_status,
                    comment=comment
                )
                notify_task_status_changed(task, old_status, new_status, user=request.user, comment=comment)
                messages.success(request, f"Task status transitioned to {task.get_status_display()}.")
            elif comment:
                TaskAuditLog.objects.create(
                    company=company,
                    task=task,
                    user=request.user,
                    action='STATUS_CHANGE',
                    from_status=old_status,
                    to_status=old_status,
                    comment=comment
                )
                notify_task_status_changed(task, old_status, old_status, user=request.user, comment=comment)
                messages.success(request, "Activity note recorded in task audit trail.")

        hours = request.POST.get('hours_spent')
        desc = request.POST.get('log_description')
        if hours and desc:
            WorkLog.objects.create(
                company=company,
                task=task,
                user=request.user,
                work_date=timezone.now().date(),
                hours_spent=Decimal(hours),
                description=desc
            )
            TaskAuditLog.objects.create(
                company=company,
                task=task,
                user=request.user,
                action='WORK_LOGGED',
                comment=f"Logged {hours} hours: {desc}"
            )
            notify_work_logged(task, user=request.user, hours=hours, description=desc)
            messages.success(request, f"Logged {hours} hours on {task.task_number}.")

        return redirect('projects:task_detail', task_id=task.id)

    check_and_escalate_overdue_tasks(company=company, project=task.project)

    return render(request, 'projects/task_detail.html', {
        'task': task,
        'work_logs': task.work_logs.select_related('user', 'user__employee_profile').all(),
        'audit_logs': task.audit_logs.select_related('user', 'user__employee_profile').all(),
        'can_complete': can_complete,
        'is_assigned': is_assigned,
        'can_change_status': can_change_status,
        'active_tab': 'projects',
        'active_sub': 'tasks'
    })


@login_required
@require_permission('projects', 'view')
def conversations(request):
    """
    WhatsApp Desktop style project messaging view with Entity Tagging,
    WhatsApp date dividers, unread message separator, and scroll-to-first-unread.
    Only assigned team members can access project conversations.
    """
    company = request.company
    membership = getattr(request, 'membership', None)
    admin_user = is_project_admin(request.user, membership)

    if admin_user:
        projects_qs = Project.objects.filter(company=company)
    else:
        projects_qs = Project.objects.filter(company=company).filter(
            Q(members__user=request.user) | Q(manager=request.user)
        ).distinct()

    # Sort projects by latest message timestamp (WhatsApp style: most recent activity first)
    projects = list(projects_qs.annotate(
        last_message_at=Max('messages__created_at')
    ).annotate(
        latest_activity=Coalesce('last_message_at', 'created_at')
    ).order_by('-latest_activity', '-id'))

    # Annotate unread counts on each project for this user
    for p in projects:
        unread_cnt = ProjectMessage.objects.filter(
            project=p
        ).exclude(
            sender=request.user
        ).exclude(
            read_receipts__user=request.user
        ).count()
        p.unread_count = unread_cnt

    selected_project_id = request.GET.get('project_id')
    active_project = None
    
    if selected_project_id:
        active_project = next((p for p in projects if str(p.id) == str(selected_project_id)), None)
        if not active_project:
            messages.error(request, "Access Denied: You are not an assigned team member of this project conversation.")
            active_project = projects[0] if projects else None
    else:
        active_project = None

    # Permissions for entity distribution
    can_view_invoices = admin_user or (membership and membership.has_permission('invoicing', 'view'))
    can_view_leads = admin_user or (membership and membership.has_permission('crm', 'view'))
    can_view_tasks = admin_user or (active_project and can_access_project(request.user, membership, active_project))

    if request.method == 'POST':
        if not active_project:
            messages.error(request, "Cannot send message: No accessible project conversation selected.")
            return redirect('projects:conversations')

        text = request.POST.get('message_text')
        entity_type = request.POST.get('entity_type', 'NONE')
        entity_id = request.POST.get('entity_id', '').strip()
        entity_label = request.POST.get('entity_label', '').strip()

        # Enforce permission checks on POST linking as well
        if entity_type == 'TASK' and not can_view_tasks:
            entity_type = 'NONE'
            entity_id = ''
            entity_label = ''
        elif entity_type == 'INVOICE' and not can_view_invoices:
            entity_type = 'NONE'
            entity_id = ''
            entity_label = ''
        elif entity_type == 'LEAD' and not can_view_leads:
            entity_type = 'NONE'
            entity_id = ''
            entity_label = ''
        elif entity_type == 'NONE':
            entity_id = ''
            entity_label = ''

        if text:
            msg = ProjectMessage.objects.create(
                project=active_project,
                sender=request.user,
                message_text=text,
                related_entity_type=entity_type,
                related_entity_id=entity_id,
                related_entity_label=entity_label
            )
            messages.success(request, "Message sent.")
            return redirect(f"{request.path}?project_id={active_project.id}")

    if active_project:
        check_and_escalate_overdue_tasks(company=company, project=active_project)

    chat_messages = []
    unread_count = 0
    first_unread_id = None

    if active_project:
        raw_msgs = list(active_project.messages.select_related('sender', 'sender__employee_profile').prefetch_related('read_receipts').all())
        read_msg_ids = set(ProjectMessageRead.objects.filter(
            message__project=active_project,
            user=request.user
        ).values_list('message_id', flat=True))

        prev_date = None
        unreads = []
        for msg in raw_msgs:
            local_dt = timezone.localtime(msg.created_at) if msg.created_at else timezone.localtime(timezone.now())
            msg.local_date = local_dt.date()
            msg.date_label = format_date_label(local_dt)
            msg.formatted_time = local_dt.strftime("%H:%M")
            msg.is_first_of_date = (prev_date != msg.local_date)
            prev_date = msg.local_date

            msg.is_mine = bool(msg.sender_id and msg.sender_id == request.user.id)
            if msg.is_mine:
                msg.is_read_by_others = any(r.user_id != request.user.id for r in msg.read_receipts.all())
                msg.is_unread = False
            else:
                msg.is_read_by_others = False
                msg.is_unread = (msg.id not in read_msg_ids)
                if msg.is_unread:
                    unreads.append(msg)

        unread_count = len(unreads)
        if unreads:
            first_unread_id = unreads[0].id
            for msg in raw_msgs:
                msg.is_first_unread = (msg.id == first_unread_id)
        else:
            for msg in raw_msgs:
                msg.is_first_unread = False

        chat_messages = raw_msgs

    return render(request, 'projects/conversations.html', {
        'projects': projects,
        'active_project': active_project,
        'chat_messages': chat_messages,
        'unread_count': unread_count,
        'first_unread_id': first_unread_id,
        'is_admin': admin_user,
        'can_view_invoices': can_view_invoices,
        'can_view_leads': can_view_leads,
        'can_view_tasks': can_view_tasks,
        'active_tab': 'chat',
        'active_sub': 'chat'
    })


@login_required
def api_chat_unread_summary(request):
    """
    Returns unread counts across all accessible projects for current user.
    Used by floating button badge and real-time poller.
    """
    company = getattr(request, 'company', None)
    if not company:
        return JsonResponse({'total_unread': 0, 'projects': []})

    membership = getattr(request, 'membership', None)
    admin_user = is_project_admin(request.user, membership)

    if admin_user:
        projects_qs = Project.objects.filter(company=company)
    else:
        projects_qs = Project.objects.filter(company=company).filter(
            Q(members__user=request.user) | Q(manager=request.user)
        ).distinct()

    # Sort projects by latest message timestamp (WhatsApp style: most recent activity first)
    projects = list(projects_qs.annotate(
        last_message_at=Max('messages__created_at')
    ).annotate(
        latest_activity=Coalesce('last_message_at', 'created_at')
    ).order_by('-latest_activity', '-id'))

    proj_list = []
    total_unread = 0

    for p in projects:
        unread_cnt = ProjectMessage.objects.filter(
            project=p
        ).exclude(
            sender=request.user
        ).exclude(
            read_receipts__user=request.user
        ).count()
        total_unread += unread_cnt

        last_msg = p.messages.order_by('-created_at').first()
        last_text = last_msg.message_text if last_msg else ""
        last_time = timezone.localtime(last_msg.created_at).strftime("%H:%M") if last_msg else ""
        last_iso = timezone.localtime(last_msg.created_at).isoformat() if last_msg else ""

        proj_list.append({
            'id': p.id,
            'code': p.code,
            'name': p.name,
            'unread_count': unread_cnt,
            'last_message': last_text,
            'last_message_time': last_time,
            'last_message_iso': last_iso,
        })

    return JsonResponse({
        'total_unread': total_unread,
        'projects': proj_list,
    })


@login_required
def api_chat_thread(request, project_id):
    """
    Fetches full message thread for floating chat widget and dynamic views.
    Includes WhatsApp date dividers, unread flags, and first unread marker.
    """
    company = getattr(request, 'company', None)
    membership = getattr(request, 'membership', None)
    admin_user = is_project_admin(request.user, membership)

    try:
        project = Project.objects.get(id=project_id, company=company)
    except Project.DoesNotExist:
        return JsonResponse({'error': 'Project not found'}, status=404)

    if not admin_user and not can_access_project(request.user, membership, project):
        return JsonResponse({'error': 'Access Denied: You are not assigned to this project'}, status=403)

    raw_msgs = list(project.messages.select_related('sender', 'sender__employee_profile').prefetch_related('read_receipts').all())
    read_msg_ids = set(ProjectMessageRead.objects.filter(
        message__project=project,
        user=request.user
    ).values_list('message_id', flat=True))

    prev_date = None
    unreads = []
    message_dicts = []

    for msg in raw_msgs:
        local_dt = timezone.localtime(msg.created_at) if msg.created_at else timezone.localtime(timezone.now())
        msg_date = local_dt.date()
        date_label = format_date_label(local_dt)
        is_first_of_date = (prev_date != msg_date)
        prev_date = msg_date

        is_mine = bool(msg.sender_id and msg.sender_id == request.user.id)
        if is_mine:
            is_read_by_others = any(r.user_id != request.user.id for r in msg.read_receipts.all())
            is_unread = False
        else:
            is_read_by_others = False
            is_unread = (msg.id not in read_msg_ids)
            if is_unread:
                unreads.append(msg.id)

        sender_name = "System"
        if msg.sender:
            emp = getattr(msg.sender, 'employee_profile', None)
            if emp and getattr(emp, 'full_name', None):
                sender_name = emp.full_name
            elif msg.sender.get_full_name():
                sender_name = msg.sender.get_full_name()
            else:
                sender_name = msg.sender.username

        message_dicts.append({
            'id': msg.id,
            'project_id': msg.project_id,
            'sender_id': msg.sender_id,
            'sender_name': sender_name,
            'is_mine': is_mine,
            'message_type': msg.message_type,
            'message_text': msg.message_text,
            'related_entity_type': msg.related_entity_type,
            'related_entity_id': msg.related_entity_id,
            'related_entity_label': msg.related_entity_label,
            'created_at': local_dt.strftime("%H:%M"),
            'date_iso': local_dt.strftime("%Y-%m-%d"),
            'date_label': date_label,
            'is_first_of_date': is_first_of_date,
            'is_unread': is_unread,
            'is_first_unread': False,
            'is_read_by_others': is_read_by_others,
        })

    first_unread_id = unreads[0] if unreads else None
    if first_unread_id:
        for md in message_dicts:
            if md['id'] == first_unread_id:
                md['is_first_unread'] = True
                break

    return JsonResponse({
        'project': {
            'id': project.id,
            'code': project.code,
            'name': project.name,
            'manager': project.manager.username if project.manager else 'Unassigned',
        },
        'unread_count': len(unreads),
        'first_unread_id': first_unread_id,
        'messages': message_dicts,
    })


@login_required
def api_chat_mark_read(request, project_id):
    """
    Marks all unread messages for request.user in the specified project as read.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    company = getattr(request, 'company', None)
    membership = getattr(request, 'membership', None)
    admin_user = is_project_admin(request.user, membership)

    try:
        project = Project.objects.get(id=project_id, company=company)
    except Project.DoesNotExist:
        return JsonResponse({'error': 'Project not found'}, status=404)

    if not admin_user and not can_access_project(request.user, membership, project):
        return JsonResponse({'error': 'Access Denied'}, status=403)

    unread_messages = ProjectMessage.objects.filter(
        project=project
    ).exclude(
        sender=request.user
    ).exclude(
        read_receipts__user=request.user
    )

    new_receipts = [
        ProjectMessageRead(message=m, user=request.user)
        for m in unread_messages
    ]
    created = ProjectMessageRead.objects.bulk_create(new_receipts, ignore_conflicts=True)

    return JsonResponse({
        'success': True,
        'marked_count': len(created),
        'project_id': project_id,
    })


@login_required
def api_chat_send(request, project_id):
    """
    Sends a message via AJAX POST.
    Automatically triggers post_save signal which broadcasts to WebSocket channels.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    company = getattr(request, 'company', None)
    membership = getattr(request, 'membership', None)
    admin_user = is_project_admin(request.user, membership)

    try:
        project = Project.objects.get(id=project_id, company=company)
    except Project.DoesNotExist:
        return JsonResponse({'error': 'Project not found'}, status=404)

    if not admin_user and not can_access_project(request.user, membership, project):
        return JsonResponse({'error': 'Access Denied'}, status=403)

    text = request.POST.get('message_text', '').strip()
    if not text:
        return JsonResponse({'error': 'Message text is required'}, status=400)

    entity_type = request.POST.get('entity_type', 'NONE')
    entity_id = request.POST.get('entity_id', '').strip()
    entity_label = request.POST.get('entity_label', '').strip()

    msg = ProjectMessage.objects.create(
        project=project,
        sender=request.user,
        message_type='CHAT',
        message_text=text,
        related_entity_type=entity_type,
        related_entity_id=entity_id,
        related_entity_label=entity_label
    )

    return JsonResponse({
        'success': True,
        'message': serialize_project_message(msg),
    })


@login_required
def task_lookup(request, task_number):
    company = request.company
    membership = getattr(request, 'membership', None)
    admin_user = is_project_admin(request.user, membership)

    task = get_object_or_404(Task, company=company, task_number=task_number)
    if not admin_user and not can_access_project(request.user, membership, task.project):
        messages.error(request, "Permission Denied: Only project team members and admins can access this task.")
        return redirect('projects:project_list')
    return redirect('projects:task_detail', task_id=task.id)


@login_required
def invoice_lookup(request, invoice_number):
    company = request.company
    membership = getattr(request, 'membership', None)
    admin_user = is_project_admin(request.user, membership)

    if not admin_user and not (membership and membership.has_permission('invoicing', 'view')):
        messages.error(request, "Permission Denied: You do not have permission to view invoices.")
        return redirect('projects:conversations')

    invoice = get_object_or_404(Invoice, company=company, invoice_number=invoice_number)
    return redirect('invoicing:invoice_detail', invoice_id=invoice.id)


@login_required
def lead_lookup(request, lead_number):
    company = request.company
    membership = getattr(request, 'membership', None)
    admin_user = is_project_admin(request.user, membership)

    if not admin_user and not (membership and membership.has_permission('crm', 'view')):
        messages.error(request, "Permission Denied: You do not have permission to view CRM leads.")
        return redirect('projects:conversations')

    lead = get_object_or_404(Lead, company=company, lead_number=lead_number)
    return redirect('crm:lead_detail', lead_id=lead.id)


@login_required
def api_entity_search(request):
    """
    Search and paginated delivery of tasks, leads, or invoices for automated chat entity linking.
    Permission rules:
      - TASK: Distributed ONLY to project team members and admins.
      - INVOICE: Distributed ONLY if user has specific 'invoicing:view' permission or is admin.
      - LEAD: Distributed ONLY if user has specific 'crm:view' permission or is admin.
    """
    company = request.company
    membership = getattr(request, 'membership', None)
    admin_user = is_project_admin(request.user, membership)

    entity_type = request.GET.get('entity_type', '').upper().strip()
    project_id = request.GET.get('project_id', '').strip()
    q = request.GET.get('q', '').strip()
    
    try:
        page = max(1, int(request.GET.get('page', 1)))
    except (ValueError, TypeError):
        page = 1
        
    try:
        page_size = max(1, min(50, int(request.GET.get('page_size', 10))))
    except (ValueError, TypeError):
        page_size = 10

    offset = (page - 1) * page_size
    results = []
    total_count = 0

    if entity_type == 'TASK':
        # Tasks will distribute ONLY to team members and admin
        if not project_id:
            return JsonResponse({
                'results': [],
                'page': page,
                'has_more': False,
                'total': 0,
                'error': 'Project ID required to distribute tasks.'
            }, status=400)

        project = Project.objects.filter(company=company, id=project_id).first()
        if not project:
            return JsonResponse({
                'results': [],
                'page': page,
                'has_more': False,
                'total': 0,
                'error': 'Project not found.'
            }, status=404)

        if not admin_user and not can_access_project(request.user, membership, project):
            return JsonResponse({
                'results': [],
                'page': page,
                'has_more': False,
                'total': 0,
                'error': 'Permission Denied: Tasks are distributed only to project team members and admins.'
            }, status=403)

        qs = Task.objects.filter(company=company, project=project)
        if q:
            qs = qs.filter(Q(task_number__icontains=q) | Q(title__icontains=q))
        
        qs = qs.order_by('-created_at', '-id')
        total_count = qs.count()
        tasks = qs[offset:offset + page_size]
        
        for task in tasks:
            results.append({
                'id': task.task_number,
                'number': task.task_number,
                'title': task.title,
                'label': f"{task.task_number} — {task.title}",
                'status': task.get_status_display(),
                'priority': task.priority,
                'subtitle': f"Status: {task.get_status_display()} | Priority: {task.priority}",
                'badge': task.status,
            })

    elif entity_type == 'LEAD':
        # Specific permission check for leads
        can_leads = admin_user or (membership and membership.has_permission('crm', 'view'))
        if not can_leads:
            return JsonResponse({
                'results': [],
                'page': page,
                'has_more': False,
                'total': 0,
                'error': 'Permission Denied: Specific CRM Lead permission required.'
            }, status=403)

        qs = Lead.objects.filter(company=company)
        if q:
            qs = qs.filter(
                Q(lead_number__icontains=q) |
                Q(company_name__icontains=q) |
                Q(contact_person__icontains=q) |
                Q(phone__icontains=q)
            )
        qs = qs.order_by('-created_at', '-id')
        total_count = qs.count()
        leads = qs[offset:offset + page_size]
        
        for lead in leads:
            results.append({
                'id': lead.lead_number,
                'number': lead.lead_number,
                'title': f"{lead.company_name} ({lead.contact_person})",
                'label': f"{lead.lead_number} — {lead.company_name}",
                'status': lead.get_status_display(),
                'priority': lead.priority,
                'subtitle': f"{lead.contact_person} | Status: {lead.get_status_display()} | Est: ₹{lead.estimated_value}",
                'badge': lead.status,
            })

    elif entity_type == 'INVOICE':
        # Specific permission check for invoices
        can_invoices = admin_user or (membership and membership.has_permission('invoicing', 'view'))
        if not can_invoices:
            return JsonResponse({
                'results': [],
                'page': page,
                'has_more': False,
                'total': 0,
                'error': 'Permission Denied: Specific Invoicing permission required.'
            }, status=403)

        qs = Invoice.objects.filter(company=company).select_related('customer')
        if q:
            qs = qs.filter(
                Q(invoice_number__icontains=q) |
                Q(customer__name__icontains=q)
            )
        qs = qs.order_by('-date', '-invoice_number')
        total_count = qs.count()
        invoices = qs[offset:offset + page_size]
        
        for inv in invoices:
            results.append({
                'id': inv.invoice_number,
                'number': inv.invoice_number,
                'title': f"{inv.customer.name} — ₹{inv.grand_total}",
                'label': f"{inv.invoice_number} — {inv.customer.name}",
                'status': inv.get_status_display(),
                'subtitle': f"Customer: {inv.customer.name} | Total: ₹{inv.grand_total} | Due: ₹{inv.balance_due}",
                'badge': inv.status,
            })

    has_more = (offset + len(results)) < total_count

    return JsonResponse({
        'results': results,
        'page': page,
        'has_more': has_more,
        'total': total_count
    })




