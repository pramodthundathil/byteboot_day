from django.db import models
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from tenants.models import Company, CompanyMembership, UserProfile, CustomRole, AuditEvent
from tenants.decorators import require_permission

def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard:index')

    if request.method == 'POST':
        u = request.POST.get('username')
        p = request.POST.get('password')
        user = authenticate(request, username=u, password=p)
        if user is not None:
            login(request, user)
            messages.success(request, f"Welcome back, {user.username}!")
            return redirect('dashboard:index')
        else:
            messages.error(request, "Invalid username or password.")

    return render(request, 'tenants/login.html')


def logout_view(request):
    logout(request)
    messages.info(request, "Logged out successfully.")
    return redirect('tenants:login')


@login_required
def switch_company(request):
    if request.method == 'POST':
        company_id = request.POST.get('company_id')
        membership = CompanyMembership.objects.filter(
            user=request.user,
            company_id=company_id,
            is_active=True
        ).first()

        if membership:
            request.session['active_company_id'] = membership.company.id
            profile = request.user.profile
            profile.active_company = membership.company
            profile.save(update_fields=['active_company'])
            messages.success(request, f"Switched active company to {membership.company.name}.")
        else:
            messages.error(request, "You do not have active membership in this company.")

    return redirect(request.META.get('HTTP_REFERER', 'dashboard:index'))


@login_required
def company_settings(request):
    company = request.company
    if not company:
        messages.error(request, "No active company context.")
        return redirect('dashboard:index')

    if request.method == 'POST':
        company.name = request.POST.get('name', company.name)
        company.tagline = request.POST.get('tagline', company.tagline)
        company.logo_url = request.POST.get('logo_url', company.logo_url)
        company.phone = request.POST.get('phone', company.phone)
        company.email = request.POST.get('email', company.email)
        company.gstin = request.POST.get('gstin', company.gstin)
        company.address = request.POST.get('address', company.address)
        company.city = request.POST.get('city', company.city)
        company.state = request.POST.get('state', company.state)
        company.save()
        messages.success(request, "Company settings updated successfully.")
        return redirect('tenants:company_settings')

    return render(request, 'tenants/company_settings.html', {
        'company': company,
        'active_tab': 'settings'
    })


@login_required
@require_permission('roles', 'view')
def role_list(request):
    company = request.company
    roles = CustomRole.objects.filter(company=company).annotate(
        member_count=models.Count('memberships')
    )
    system_roles = [
        {
            'name': 'Company Admin',
            'code': 'COMPANY_ADMIN',
            'desc': 'Complete full access across all workspace modules, accounting, users, settings, and company configurations.',
            'scope': 'Full Workspace Access',
            'type': 'Preset System Role',
        },
        {
            'name': 'Accountant',
            'code': 'ACCOUNTANT',
            'desc': 'Access to Invoicing, Payment Collection, General Ledger, Chart of Accounts, and Journal Entries.',
            'scope': 'Finance & Accounting',
            'type': 'Preset System Role',
        },
        {
            'name': 'HR Manager',
            'code': 'HR',
            'desc': 'Access to Employee Directory, Onboarding, Leave Approvals, and Attendance Management.',
            'scope': 'Human Resources',
            'type': 'Preset System Role',
        },
        {
            'name': 'Team Lead / Manager',
            'code': 'MANAGER',
            'desc': 'Access to CRM Leads & Opportunities, Project & Task management, and Team Leave approvals.',
            'scope': 'Operations & Projects',
            'type': 'Preset System Role',
        },
        {
            'name': 'Staff Member',
            'code': 'STAFF',
            'desc': 'Standard workspace access to view assigned projects, update task statuses, and submit leave requests.',
            'scope': 'Standard Tasks',
            'type': 'Preset System Role',
        },
    ]
    return render(request, 'tenants/role_list.html', {
        'roles': roles,
        'system_roles': system_roles,
        'active_tab': 'settings',
        'active_sub': 'roles'
    })


@login_required
@require_permission('roles', 'create')
def role_create(request):
    company = request.company
    membership = request.membership

    # Ensure only Company Admin can create custom roles
    if membership and membership.role not in ('COMPANY_ADMIN', 'PLATFORM_ADMIN') and not request.user.is_superuser:
        messages.error(request, "Only Company Admin can create custom roles and permission policies.")
        return redirect('tenants:role_list')

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        desc = request.POST.get('description', '').strip()

        if not name:
            messages.error(request, "Role name is required.")
            return redirect('tenants:role_create')

        if CustomRole.objects.filter(company=company, name=name).exists():
            messages.error(request, f"A custom role named '{name}' already exists.")
            return redirect('tenants:role_create')

        # Build granular permission matrix JSON from form checkboxes
        modules = ['crm', 'invoicing', 'accounting', 'hr', 'projects', 'reports']
        actions = ['view', 'create', 'edit', 'delete', 'approve', 'assign', 'post', 'record_payment', 'convert']

        perms_dict = {}
        for mod in modules:
            perms_dict[mod] = {}
            for act in actions:
                param_key = f"perm_{mod}_{act}"
                perms_dict[mod][act] = (request.POST.get(param_key) == 'on' or request.POST.get(param_key) == '1')

        role = CustomRole.objects.create(
            company=company,
            name=name,
            description=desc,
            permissions=perms_dict
        )

        AuditEvent.log(
            company=company,
            actor=request.user,
            action='CREATE',
            module='tenants',
            model_name='CustomRole',
            object_id=str(role.id),
            object_repr=role.name,
            changes={'permissions': perms_dict}
        )

        messages.success(request, f"Custom Role '{role.name}' created with custom permission policies.")
        return redirect('tenants:role_list')

    default_permissions = {
        'projects': {'view': True, 'create': True, 'edit': True, 'assign': True, 'delete': False},
        'hr': {'view': True, 'create': False, 'edit': False, 'approve': False, 'delete': False},
        'crm': {'view': True, 'create': True, 'edit': True, 'assign': True, 'convert': True, 'delete': False},
        'invoicing': {'view': True, 'create': True, 'edit': False, 'record_payment': True, 'post': True, 'delete': False},
        'accounting': {'view': False, 'create': False, 'edit': False, 'post': False, 'delete': False},
        'reports': {'view': True},
    }

    return render(request, 'tenants/role_form.html', {
        'role': None,
        'permissions': default_permissions,
        'active_tab': 'settings',
        'active_sub': 'roles'
    })


@login_required
@require_permission('roles', 'edit')
def role_edit(request, role_id):
    company = request.company
    membership = request.membership

    if membership and membership.role not in ('COMPANY_ADMIN', 'PLATFORM_ADMIN') and not request.user.is_superuser:
        messages.error(request, "Only Company Admin can edit custom roles and permission policies.")
        return redirect('tenants:role_list')

    role = get_object_or_404(CustomRole, id=role_id, company=company)

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        desc = request.POST.get('description', '').strip()

        if not name:
            messages.error(request, "Role name is required.")
            return render(request, 'tenants/role_form.html', {
                'role': role,
                'permissions': role.permissions or {},
                'active_tab': 'settings',
                'active_sub': 'roles'
            })

        if CustomRole.objects.filter(company=company, name=name).exclude(id=role.id).exists():
            messages.error(request, f"A custom role named '{name}' already exists.")
            return render(request, 'tenants/role_form.html', {
                'role': role,
                'permissions': role.permissions or {},
                'active_tab': 'settings',
                'active_sub': 'roles'
            })

        modules = ['crm', 'invoicing', 'accounting', 'hr', 'projects', 'reports']
        actions = ['view', 'create', 'edit', 'delete', 'approve', 'assign', 'post', 'record_payment', 'convert']

        perms_dict = {}
        for mod in modules:
            perms_dict[mod] = {}
            for act in actions:
                param_key = f"perm_{mod}_{act}"
                perms_dict[mod][act] = (request.POST.get(param_key) == 'on' or request.POST.get(param_key) == '1')

        old_perms = role.permissions
        role.name = name
        role.description = desc
        role.permissions = perms_dict
        role.save()

        AuditEvent.log(
            company=company,
            actor=request.user,
            action='UPDATE',
            module='tenants',
            model_name='CustomRole',
            object_id=str(role.id),
            object_repr=role.name,
            changes={'old': old_perms, 'new': perms_dict}
        )

        messages.success(request, f"Permissions and configuration for role '{role.name}' updated successfully.")
        return redirect('tenants:role_list')

    return render(request, 'tenants/role_form.html', {
        'role': role,
        'permissions': role.permissions or {},
        'active_tab': 'settings',
        'active_sub': 'roles'
    })


@login_required
@require_permission('roles', 'delete')
def role_delete(request, role_id):
    company = request.company
    membership = request.membership

    if membership and membership.role not in ('COMPANY_ADMIN', 'PLATFORM_ADMIN') and not request.user.is_superuser:
        messages.error(request, "Only Company Admin can delete custom roles.")
        return redirect('tenants:role_list')

    role = get_object_or_404(CustomRole, id=role_id, company=company)

    if request.method == 'POST':
        role_name = role.name
        # Reassign memberships assigned to this custom role back to STAFF
        affected_memberships = CompanyMembership.objects.filter(company=company, custom_role=role)
        affected_count = affected_memberships.count()
        affected_memberships.update(role='STAFF', custom_role=None)

        role.delete()

        AuditEvent.log(
            company=company,
            actor=request.user,
            action='DELETE',
            module='tenants',
            model_name='CustomRole',
            object_id=str(role_id),
            object_repr=role_name,
            changes={'reassigned_members': affected_count}
        )

        messages.success(
            request, 
            f"Custom Role '{role_name}' deleted successfully.{f' {affected_count} assigned members reverted to Staff role.' if affected_count else ''}"
        )
        return redirect('tenants:role_list')

    return redirect('tenants:role_list')
