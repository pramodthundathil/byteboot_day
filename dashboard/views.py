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

    # CRM Metrics
    leads_count = Lead.objects.filter(company=company).count()
    won_leads = Lead.objects.filter(company=company, status='WON').count()
    conversion_rate = round((won_leads / leads_count * 100), 1) if leads_count > 0 else 0

    # Sales & Invoicing
    invoices = Invoice.objects.filter(company=company)
    total_sales = sum((inv.grand_total for inv in invoices if inv.status != 'CANCELLED'), Decimal('0.00'))
    total_receivables = sum((inv.balance_due for inv in invoices if inv.status in ['ISSUED', 'PARTIALLY_PAID', 'OVERDUE']), Decimal('0.00'))
    
    # Projects & Tasks
    membership = getattr(request, 'membership', None)
    is_proj_admin = (
        request.user.is_superuser or
        (membership and (
            membership.role in ('COMPANY_ADMIN', 'PLATFORM_ADMIN') or
            membership.has_permission('projects', 'assign') or
            membership.has_permission('projects', 'approve')
        ))
    )
    if is_proj_admin:
        active_projects = Project.objects.filter(company=company, status='IN_PROGRESS').count()
    else:
        active_projects = Project.objects.filter(
            company=company,
            status='IN_PROGRESS'
        ).filter(
            Q(members__user=request.user) | Q(manager=request.user)
        ).distinct().count()

    pending_tasks = Task.objects.filter(company=company, status__in=['TO_DO', 'IN_PROGRESS', 'IN_REVIEW']).count()

    # HR & Leave
    active_employees = Employee.objects.filter(company=company, status='ACTIVE').count()
    pending_leaves = LeaveRequest.objects.filter(company=company, status='PENDING').count()

    # Recent Audit Log
    recent_activity = AuditEvent.objects.filter(company=company)[:6]

    # Recent Invoices
    recent_invoices = invoices[:5]

    return render(request, 'dashboard/index.html', {
        'leads_count': leads_count,
        'won_leads': won_leads,
        'conversion_rate': conversion_rate,
        'total_sales': total_sales,
        'total_receivables': total_receivables,
        'active_projects': active_projects,
        'pending_tasks': pending_tasks,
        'active_employees': active_employees,
        'pending_leaves': pending_leaves,
        'recent_activity': recent_activity,
        'recent_invoices': recent_invoices,
        'active_tab': 'dashboard',
        'active_sub': 'dashboard'
    })


@login_required
def reports(request):
    company = request.company
    return render(request, 'dashboard/reports.html', {
        'active_tab': 'reports',
        'active_sub': 'reports'
    })
