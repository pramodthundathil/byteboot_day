from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages
from decimal import Decimal
from django.utils import timezone
from django.db.models import Q, Count
from projects.models import Project, ProjectMember, Task, WorkLog, ProjectMessage
from crm.models import Customer
from tenants.decorators import require_permission

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
            estimated_hours=Decimal(request.POST.get('estimated_hours', '8.00') or '0.00'),
            due_date=request.POST.get('due_date') or None
        )
        task.assignees.set(selected_users)

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
            task.estimated_hours = Decimal(est_hrs)
            
        due_d = request.POST.get('due_date')
        task.due_date = due_d if due_d else None

        task.save()
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

    if request.method == 'POST':
        new_status = request.POST.get('status')
        if new_status:
            task.status = new_status
            if new_status == 'DONE':
                task.completed_at = timezone.now()
            task.save()
            messages.success(request, f"Task {task.task_number} status updated to {task.get_status_display()}.")

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
            messages.success(request, f"Logged {hours} hours on {task.task_number}.")

        return redirect('projects:task_detail', task_id=task.id)

    return render(request, 'projects/task_detail.html', {
        'task': task,
        'work_logs': task.work_logs.all(),
        'active_tab': 'projects',
        'active_sub': 'tasks'
    })


@login_required
@require_permission('projects', 'view')
def conversations(request):
    """
    WhatsApp Desktop style project messaging view with Entity Tagging.
    Only assigned team members can access project conversations.
    """
    company = request.company
    membership = getattr(request, 'membership', None)
    admin_user = is_project_admin(request.user, membership)

    if admin_user:
        projects = Project.objects.filter(company=company)
    else:
        projects = Project.objects.filter(company=company).filter(
            Q(members__user=request.user) | Q(manager=request.user)
        ).distinct()

    selected_project_id = request.GET.get('project_id')
    active_project = None
    
    if selected_project_id:
        active_project = projects.filter(id=selected_project_id).first()
        if not active_project:
            messages.error(request, "Access Denied: You are not an assigned team member of this project conversation.")
            active_project = projects.first()
    else:
        active_project = projects.first()

    if request.method == 'POST':
        if not active_project:
            messages.error(request, "Cannot send message: No accessible project conversation selected.")
            return redirect('projects:conversations')

        text = request.POST.get('message_text')
        entity_type = request.POST.get('entity_type', 'NONE')
        entity_id = request.POST.get('entity_id', '')
        entity_label = request.POST.get('entity_label', '')

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

    chat_messages = active_project.messages.all() if active_project else []

    return render(request, 'projects/conversations.html', {
        'projects': projects,
        'active_project': active_project,
        'chat_messages': chat_messages,
        'is_admin': admin_user,
        'active_tab': 'chat',
        'active_sub': 'chat'
    })

