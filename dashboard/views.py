from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from decimal import Decimal
from django.db.models import Q
from crm.models import Lead, Customer
from invoicing.models import Invoice, CustomerPayment
from accounting.models import Account, JournalEntry
from hr.models import Employee, LeaveRequest
from projects.models import Project, Task
from tenants.models import AuditEvent

@login_required
def index(request):
    company = request.company
    if not company:
        return render(request, 'dashboard/index.html', {'active_tab': 'dashboard', 'active_sub': 'dashboard'})

    membership = getattr(request, 'membership', None)
    is_admin = (
        request.user.is_superuser or 
        (membership and membership.role in ('COMPANY_ADMIN', 'PLATFORM_ADMIN'))
    )

    # Granular view authorizations based on roles/custom policies
    has_crm = is_admin or (membership and membership.has_permission('crm', 'view'))
    has_invoicing = is_admin or (membership and membership.has_permission('invoicing', 'view'))
    has_accounting = is_admin or (membership and membership.has_permission('accounting', 'view'))
    has_hr = is_admin or (membership and membership.has_permission('hr', 'view'))
    has_projects = is_admin or (membership and membership.has_permission('projects', 'view'))
    has_roles = is_admin or (membership and membership.has_permission('roles', 'view'))

    # Granular action authorizations for action bar
    can_create_lead = is_admin or (membership and membership.has_permission('crm', 'create'))
    can_create_invoice = is_admin or (membership and membership.has_permission('invoicing', 'create'))
    can_create_journal = is_admin or (membership and membership.has_permission('accounting', 'create'))
    can_create_project = is_admin or (membership and membership.has_permission('projects', 'create'))
    can_create_task = is_admin or (membership and membership.has_permission('projects', 'create'))
    can_create_employee = is_admin or (membership and membership.has_permission('hr', 'create'))
    can_approve_leave = is_admin or (membership and membership.has_permission('hr', 'approve'))

    # Determine user role display & category
    if request.user.is_superuser:
        role_title = "Platform Super Admin"
        role_type = "SUPERUSER"
        role_desc = "Unrestricted executive system administration & multi-tenant access"
    elif membership:
        if membership.role == 'CUSTOM' and membership.custom_role:
            role_title = f"{membership.custom_role.name}"
            role_type = "CUSTOM"
            role_desc = membership.custom_role.description or "Custom granular role policy"
        else:
            role_title = membership.get_role_display()
            role_type = membership.role
            role_desc = {
                'COMPANY_ADMIN': 'Full company administrative & financial oversight',
                'ACCOUNTANT': 'Financial ledgers, invoicing, receivables & journal entries',
                'HR': 'Workforce management, onboarding, leaves & attendance',
                'MANAGER': 'Project leadership, task assignments & team approvals',
                'STAFF': 'Assigned project operations & personal task workspace',
            }.get(membership.role, 'Workspace operations')
    else:
        role_title = "Workspace Member"
        role_type = "STAFF"
        role_desc = "Standard workspace participant"

    context = {
        'role_title': role_title,
        'role_type': role_type,
        'role_desc': role_desc,
        'is_admin': is_admin,
        'has_crm': has_crm,
        'has_invoicing': has_invoicing,
        'has_accounting': has_accounting,
        'has_hr': has_hr,
        'has_projects': has_projects,
        'has_roles': has_roles,
        'can_create_lead': can_create_lead,
        'can_create_invoice': can_create_invoice,
        'can_create_journal': can_create_journal,
        'can_create_project': can_create_project,
        'can_create_task': can_create_task,
        'can_create_employee': can_create_employee,
        'can_approve_leave': can_approve_leave,
        'active_tab': 'dashboard',
        'active_sub': 'dashboard',
    }

    # CRM Metrics & Recent Leads
    if has_crm:
        leads_qs = Lead.objects.filter(company=company)
        leads_count = leads_qs.count()
        won_leads = leads_qs.filter(status='WON').count()
        conversion_rate = round((won_leads / leads_count * 100), 1) if leads_count > 0 else 0
        recent_leads = leads_qs.order_by('-created_at')[:5]
        context.update({
            'leads_count': leads_count,
            'won_leads': won_leads,
            'conversion_rate': conversion_rate,
            'recent_leads': recent_leads,
        })

    # Sales & Invoicing Metrics & Recent Invoices
    if has_invoicing:
        invoices = Invoice.objects.filter(company=company).select_related('customer')
        total_sales = sum((inv.grand_total for inv in invoices if inv.status != 'CANCELLED'), Decimal('0.00'))
        total_receivables = sum((inv.balance_due for inv in invoices if inv.status in ['ISSUED', 'PARTIALLY_PAID', 'OVERDUE']), Decimal('0.00'))
        recent_invoices = invoices.order_by('-created_at')[:5]
        overdue_count = invoices.filter(status='OVERDUE').count()
        context.update({
            'total_sales': total_sales,
            'total_receivables': total_receivables,
            'recent_invoices': recent_invoices,
            'overdue_count': overdue_count,
        })

    # Accounting & General Ledger
    if has_accounting:
        accounts_count = Account.objects.filter(company=company, is_active=True).count()
        journals_count = JournalEntry.objects.filter(company=company).count()
        recent_journals = JournalEntry.objects.filter(company=company).order_by('-created_at')[:5]
        context.update({
            'accounts_count': accounts_count,
            'journals_count': journals_count,
            'recent_journals': recent_journals,
        })

    # Projects & Tasks
    if has_projects:
        is_proj_admin = is_admin or (membership and (
            membership.has_permission('projects', 'assign') or 
            membership.has_permission('projects', 'approve')
        ))
        if is_proj_admin:
            projects_qs = Project.objects.filter(company=company)
            active_projects = projects_qs.filter(status='IN_PROGRESS').count()
            recent_projects = projects_qs.order_by('-created_at')[:5]
        else:
            projects_qs = Project.objects.filter(company=company).filter(
                Q(members__user=request.user) | Q(manager=request.user)
            ).distinct()
            active_projects = projects_qs.filter(status='IN_PROGRESS').count()
            recent_projects = projects_qs.order_by('-created_at')[:5]

        # My Assigned Tasks (for personal staff / manager tracker)
        my_tasks = Task.objects.filter(
            company=company
        ).filter(
            Q(assigned_to=request.user) | Q(assignees=request.user)
        ).exclude(
            status='DONE'
        ).select_related('project').distinct().order_by('-created_at')[:6]

        pending_tasks = Task.objects.filter(
            company=company, 
            status__in=['TO_DO', 'IN_PROGRESS', 'IN_REVIEW']
        ).count()

        context.update({
            'active_projects': active_projects,
            'recent_projects': recent_projects,
            'my_tasks': my_tasks,
            'my_tasks_count': my_tasks.count(),
            'pending_tasks': pending_tasks,
            'is_proj_admin': is_proj_admin,
        })

    # HR & Leave Applications
    if has_hr:
        active_employees = Employee.objects.filter(company=company, status='ACTIVE').count()
        pending_leaves_qs = LeaveRequest.objects.filter(company=company, status='PENDING').select_related('employee', 'leave_type')
        pending_leaves = pending_leaves_qs.count()
        recent_leaves = pending_leaves_qs.order_by('-created_at')[:5]
        context.update({
            'active_employees': active_employees,
            'pending_leaves': pending_leaves,
            'recent_leaves': recent_leaves,
        })
    else:
        # Standard staff can view their own leave status
        emp_obj = Employee.objects.filter(company=company, user=request.user).first()
        if emp_obj:
            my_leaves = LeaveRequest.objects.filter(company=company, employee=emp_obj).select_related('leave_type').order_by('-created_at')[:4]
            context['my_leaves'] = my_leaves

    # Live Security & Audit Event Stream (Admins or Roles management only)
    if is_admin or has_roles:
        recent_activity = AuditEvent.objects.filter(company=company).select_related('actor')[:6]
        context['recent_activity'] = recent_activity

    return render(request, 'dashboard/index.html', context)


@login_required
def reports(request):
    company = request.company
    return render(request, 'dashboard/reports.html', {
        'active_tab': 'reports',
        'active_sub': 'reports'
    })
