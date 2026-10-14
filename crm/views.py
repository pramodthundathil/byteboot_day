from datetime import datetime, time, date, timedelta
from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.utils import timezone
from django.db.models import Q, F

from crm.models import Lead, LeadFollowup, Customer, CustomerContact
from crm.services import (
    convert_lead_to_customer,
    record_lead_followup,
    assign_lead_to_staff,
    bulk_assign_leads,
    bulk_update_lead_status,
    bulk_update_lead_priority,
    bulk_delete_leads,
    generate_sample_leads_csv,
    generate_sample_leads_excel,
    parse_and_import_leads,
    get_staff_crm_performance,
    get_crm_360_analytics_data,
    generate_crm_excel_report,
    generate_crm_pdf_report
)
from tenants.decorators import require_permission
from tenants.models import AuditEvent, CompanyMembership


def is_crm_admin(user, membership):
    """
    Returns True if user has administrative or assignment privileges in CRM
    """
    if user.is_superuser:
        return True
    if membership and (
        membership.role in ('COMPANY_ADMIN', 'PLATFORM_ADMIN') or
        membership.has_permission('crm', 'assign') or
        membership.has_permission('crm', 'manage')
    ):
        return True
    return False


def can_delete_lead(user, membership):
    """
    Returns True if user is an administrator or has explicit delete permissions in CRM
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if membership and (
        membership.role in ('COMPANY_ADMIN', 'PLATFORM_ADMIN') or
        membership.has_permission('crm', 'delete') or
        membership.has_permission('crm', 'manage')
    ):
        return True
    return False


def get_assignable_users(company):
    """
    Returns list of active company staff and managers available for lead assignment
    """
    return User.objects.filter(
        memberships__company=company,
        memberships__is_active=True
    ).select_related('employee_profile').distinct().order_by('first_name', 'last_name', 'username')


@login_required
@require_permission('crm', 'view')
def followup_dashboard(request):
    """
    Specialized CRM Follow-up & Daily Reminder Dashboard:
    - Today's Follow-ups
    - Past / Overdue Follow-ups
    - Upcoming Follow-ups
    - Unscheduled Leads
    - Fast 1-click Follow-up Logger
    """
    company = request.company
    membership = getattr(request, 'membership', None)
    admin_mode = is_crm_admin(request.user, membership)

    now = timezone.localtime(timezone.now())
    today = now.date()
    start_of_today = timezone.make_aware(datetime.combine(today, time.min))
    end_of_today = timezone.make_aware(datetime.combine(today, time.max))

    # Base queryset according to role: staff sees own by default unless all requested
    base_leads = Lead.objects.filter(company=company)
    staff_id = request.GET.get('staff_id', '')

    if not admin_mode:
        # Regular staff sees their assigned leads, unassigned leads, or leads they created
        base_leads = base_leads.filter(
            Q(assigned_to=request.user) | Q(created_by=request.user)
        )
    elif staff_id:
        if staff_id == 'unassigned':
            base_leads = base_leads.filter(assigned_to__isnull=True)
        else:
            base_leads = base_leads.filter(assigned_to_id=staff_id)

    # Search & priority filters
    query = request.GET.get('q', '').strip()
    priority_filter = request.GET.get('priority', '')
    if query:
        base_leads = base_leads.filter(
            Q(company_name__icontains=query) |
            Q(contact_person__icontains=query) |
            Q(phone__icontains=query) |
            Q(email__icontains=query) |
            Q(lead_number__icontains=query)
        )
    if priority_filter:
        base_leads = base_leads.filter(priority=priority_filter)

    # Compute live counter metrics
    overdue_count = base_leads.filter(
        next_followup_date__lt=start_of_today
    ).exclude(status__in=['WON', 'LOST']).count()

    today_count = base_leads.filter(
        next_followup_date__range=(start_of_today, end_of_today)
    ).exclude(status__in=['WON', 'LOST']).count()

    upcoming_count = base_leads.filter(
        next_followup_date__gt=end_of_today
    ).exclude(status__in=['WON', 'LOST']).count()

    unscheduled_count = base_leads.filter(
        next_followup_date__isnull=True
    ).exclude(status__in=['WON', 'LOST']).count()

    won_count = base_leads.filter(status='WON').count()

    # Determine active tab
    selected_tab = request.GET.get('tab', '')
    if not selected_tab:
        if today_count > 0:
            selected_tab = 'today'
        elif overdue_count > 0:
            selected_tab = 'overdue'
        else:
            selected_tab = 'all'

    # Filter by selected tab
    if selected_tab == 'overdue':
        leads_qs = base_leads.filter(
            next_followup_date__lt=start_of_today
        ).exclude(status__in=['WON', 'LOST']).order_by('next_followup_date')
    elif selected_tab == 'today':
        leads_qs = base_leads.filter(
            next_followup_date__range=(start_of_today, end_of_today)
        ).exclude(status__in=['WON', 'LOST']).order_by('next_followup_date')
    elif selected_tab == 'upcoming':
        leads_qs = base_leads.filter(
            next_followup_date__gt=end_of_today
        ).exclude(status__in=['WON', 'LOST']).order_by('next_followup_date')
    elif selected_tab == 'unscheduled':
        leads_qs = base_leads.filter(
            next_followup_date__isnull=True
        ).exclude(status__in=['WON', 'LOST']).order_by('-created_at')
    else:
        leads_qs = base_leads.exclude(status__in=['WON', 'LOST']).order_by('-updated_at')

    # Pagination for speed
    leads_qs = leads_qs.select_related('assigned_to', 'created_by')
    paginator = Paginator(leads_qs, 15)
    page_number = request.GET.get('page', 1)
    try:
        leads_page = paginator.get_page(page_number)
    except Exception:
        leads_page = paginator.get_page(1)

    assignable_users = get_assignable_users(company) if admin_mode else []

    return render(request, 'crm/followup_dashboard.html', {
        'leads': leads_page,
        'selected_tab': selected_tab,
        'overdue_count': overdue_count,
        'today_count': today_count,
        'upcoming_count': upcoming_count,
        'unscheduled_count': unscheduled_count,
        'won_count': won_count,
        'query': query,
        'priority_filter': priority_filter,
        'staff_id': staff_id,
        'is_admin': admin_mode,
        'assignable_users': assignable_users,
        'today': today,
        'tomorrow_date': (today + timedelta(days=1)).strftime('%Y-%m-%d'),
        'active_tab': 'crm',
        'active_sub': 'followup_dashboard'
    })


@login_required
@require_permission('crm', 'view')
def lead_list(request):
    """
    Main CRM Leads Directory:
    - Search, Pipeline Filters, Priority Filters
    - Backend Pagination with date/value sorting
    - Bulk Action Capabilities
    """
    company = request.company
    membership = getattr(request, 'membership', None)
    admin_mode = is_crm_admin(request.user, membership)

    status_filter = request.GET.get('status', '')
    priority_filter = request.GET.get('priority', '')
    staff_filter = request.GET.get('staff', '')
    fup_filter = request.GET.get('fup', '')
    query = request.GET.get('q', '').strip()
    sort_order = request.GET.get('sort', '')

    leads = Lead.objects.filter(company=company)

    # Filter visibility based on role
    if not admin_mode:
        # By default staff view their assigned leads, unassigned or self-created leads
        leads = leads.filter(
            Q(assigned_to=request.user) | Q(created_by=request.user)
        )
    elif staff_filter:
        if staff_filter == 'unassigned':
            leads = leads.filter(assigned_to__isnull=True)
        else:
            leads = leads.filter(assigned_to_id=staff_filter)

    is_lost_view = (status_filter == 'LOST')
    is_won_view = (status_filter == 'WON')
    if is_lost_view:
        leads = leads.filter(status='LOST')
        active_sub = 'lost_leads'
    elif is_won_view:
        leads = leads.filter(status='WON')
        active_sub = 'won_leads'
    elif status_filter:
        leads = leads.filter(status=status_filter)
        active_sub = 'leads'
    else:
        # Main list strictly excludes LOST and WON leads
        leads = leads.exclude(status__in=['LOST', 'WON'])
        active_sub = 'leads'

    if priority_filter:
        leads = leads.filter(priority=priority_filter)
    if query:
        leads = leads.filter(
            Q(company_name__icontains=query) |
            Q(contact_person__icontains=query) |
            Q(phone__icontains=query) |
            Q(email__icontains=query) |
            Q(lead_number__icontains=query) |
            Q(service_interested__icontains=query)
        )

    # Follow-up status filter
    now = timezone.localtime(timezone.now())
    today = now.date()
    start_of_today = timezone.make_aware(datetime.combine(today, time.min))
    end_of_today = timezone.make_aware(datetime.combine(today, time.max))

    if fup_filter == 'overdue':
        leads = leads.filter(next_followup_date__lt=start_of_today).exclude(status__in=['WON', 'LOST'])
    elif fup_filter == 'today':
        leads = leads.filter(next_followup_date__range=(start_of_today, end_of_today)).exclude(status__in=['WON', 'LOST'])
    elif fup_filter == 'upcoming':
        leads = leads.filter(next_followup_date__gt=end_of_today).exclude(status__in=['WON', 'LOST'])
    elif fup_filter == 'unscheduled':
        leads = leads.filter(next_followup_date__isnull=True).exclude(status__in=['WON', 'LOST'])

    # Sorting (Defaults to earliest/due scheduled follow-up first, with unscheduled at bottom)
    valid_sorts = {
        'followup_earliest': 'followup_earliest',
        'followup_due': 'followup_earliest',
        'followup_overdue': 'followup_earliest',
        'next_followup_date': 'followup_earliest',
        'followup_latest': 'followup_latest',
        '-next_followup_date': 'followup_latest',
        '-created_at': '-created_at',
        'created_at': 'created_at',
        '-estimated_value': '-estimated_value',
        'estimated_value': 'estimated_value',
        'company_name': 'company_name'
    }
    sort_choice = valid_sorts.get(sort_order, 'followup_earliest')
    if sort_choice in ('followup_latest', '-next_followup_date'):
        leads = leads.order_by(F('next_followup_date').desc(nulls_last=True), '-created_at')
    elif sort_choice in ('followup_earliest', 'followup_due', 'followup_overdue', 'next_followup_date'):
        leads = leads.order_by(F('next_followup_date').asc(nulls_last=True), '-created_at')
    else:
        leads = leads.order_by(sort_choice)
    leads = leads.select_related('assigned_to', 'created_by')

    # Total counts for status tabs
    all_company_leads = Lead.objects.filter(company=company)
    total_leads_count = all_company_leads.count()
    active_leads_count = all_company_leads.exclude(status__in=['LOST', 'WON']).count()
    new_count = all_company_leads.filter(status='NEW').count()
    contacted_count = all_company_leads.filter(status='CONTACTED').count()
    followup_req_count = all_company_leads.filter(status='FOLLOW_UP').count()
    qualified_count = all_company_leads.filter(status='QUALIFIED').count()
    proposal_sent_count = all_company_leads.filter(status='PROPOSAL_SENT').count()
    negotiation_count = all_company_leads.filter(status='NEGOTIATION').count()
    won_count = all_company_leads.filter(status='WON').count()
    lost_count = all_company_leads.filter(status='LOST').count()

    # Backend Pagination
    paginator = Paginator(leads, 15)
    page_number = request.GET.get('page', 1)
    try:
        leads_page = paginator.get_page(page_number)
    except Exception:
        leads_page = paginator.get_page(1)

    assignable_users = get_assignable_users(company)

    return render(request, 'crm/lead_list.html', {
        'leads': leads_page,
        'status_filter': status_filter,
        'is_lost_view': is_lost_view,
        'is_won_view': is_won_view,
        'priority_filter': priority_filter,
        'staff_filter': staff_filter,
        'fup_filter': fup_filter,
        'query': query,
        'sort_order': sort_order or 'followup_earliest',
        'total_leads_count': total_leads_count,
        'active_leads_count': active_leads_count,
        'new_count': new_count,
        'contacted_count': contacted_count,
        'followup_req_count': followup_req_count,
        'qualified_count': qualified_count,
        'proposal_sent_count': proposal_sent_count,
        'negotiation_count': negotiation_count,
        'won_count': won_count,
        'lost_count': lost_count,
        'is_admin': admin_mode,
        'can_delete': can_delete_lead(request.user, membership),
        'assignable_users': assignable_users,
        'tomorrow_date': (now.date() + timedelta(days=1)).strftime('%Y-%m-%d'),
        'status_choices': Lead.STATUS_CHOICES,
        'priority_choices': Lead.PRIORITY_CHOICES,
        'active_tab': 'crm',
        'active_sub': active_sub
    })


@login_required
@require_permission('crm', 'create')
def lead_create(request):
    """
    Creates a new lead.
    - Admin/Manager can assign to any staff member
    - Staff creates lead -> automatically assigned to themselves
    """
    company = request.company
    membership = getattr(request, 'membership', None)
    admin_mode = is_crm_admin(request.user, membership)

    if request.method == 'POST':
        count = Lead.objects.filter(company=company).count() + 1
        lead_num = f"LD-{10000 + count}"
        while Lead.objects.filter(company=company, lead_number=lead_num).exists():
            count += 1
            lead_num = f"LD-{10000 + count}"

        # Assignment resolution
        assigned_user = request.user
        if admin_mode:
            assigned_id = request.POST.get('assigned_to')
            if assigned_id:
                assigned_user = User.objects.filter(id=assigned_id, memberships__company=company).first()
            elif assigned_id == '':
                assigned_user = None

        # Next follow-up date parsing
        status = request.POST.get('status', 'NEW')
        next_fup_str = request.POST.get('next_followup_date')
        next_fup = None
        if status != 'LOST' and next_fup_str:
            try:
                dt = datetime.fromisoformat(next_fup_str)
                next_fup = timezone.make_aware(dt) if timezone.is_naive(dt) else dt
            except Exception:
                pass

        lead = Lead.objects.create(
            company=company,
            lead_number=lead_num,
            company_name=request.POST.get('company_name'),
            contact_person=request.POST.get('contact_person'),
            phone=request.POST.get('phone'),
            email=request.POST.get('email', ''),
            address=request.POST.get('address', ''),
            source=request.POST.get('source', 'Website'),
            industry=request.POST.get('industry', ''),
            service_interested=request.POST.get('service_interested', 'Software Development ERP'),
            estimated_value=Decimal(request.POST.get('estimated_value', '0.00') or '0.00'),
            priority=request.POST.get('priority', 'MEDIUM'),
            status=request.POST.get('status', 'NEW'),
            pipeline_stage=request.POST.get('status', 'NEW'),
            notes=request.POST.get('notes', ''),
            next_followup_date=next_fup,
            created_by=request.user,
            assigned_to=assigned_user
        )

        AuditEvent.log(
            company=company,
            actor=request.user,
            action='CREATE',
            module='crm',
            model_name='Lead',
            object_id=lead.id,
            object_repr=str(lead),
            changes={
                'assigned_to': assigned_user.username if assigned_user else "Unassigned",
                'estimated_value': str(lead.estimated_value)
            }
        )

        messages.success(request, f"Lead {lead.lead_number} ({lead.company_name}) created and assigned successfully.")
        return redirect('crm:lead_detail', lead_id=lead.id)

    assignable_users = get_assignable_users(company) if admin_mode else []

    return render(request, 'crm/lead_form.html', {
        'is_admin': admin_mode,
        'assignable_users': assignable_users,
        'status_choices': Lead.STATUS_CHOICES,
        'priority_choices': Lead.PRIORITY_CHOICES,
        'active_tab': 'crm',
        'active_sub': 'leads'
    })


@login_required
@require_permission('crm', 'edit')
def lead_edit(request, lead_id):
    """
    Edits an existing lead record
    """
    company = request.company
    lead = get_object_or_404(Lead, id=lead_id, company=company)
    membership = getattr(request, 'membership', None)
    admin_mode = is_crm_admin(request.user, membership)

    if request.method == 'POST':
        lead.company_name = request.POST.get('company_name', lead.company_name)
        lead.contact_person = request.POST.get('contact_person', lead.contact_person)
        lead.phone = request.POST.get('phone', lead.phone)
        lead.email = request.POST.get('email', lead.email)
        lead.address = request.POST.get('address', lead.address)
        lead.source = request.POST.get('source', lead.source)
        lead.industry = request.POST.get('industry', lead.industry)
        lead.service_interested = request.POST.get('service_interested', lead.service_interested)
        lead.estimated_value = Decimal(request.POST.get('estimated_value', '0.00') or '0.00')
        lead.priority = request.POST.get('priority', lead.priority)
        lead.status = request.POST.get('status', lead.status)
        lead.notes = request.POST.get('notes', lead.notes)

        # Handle next follow-up date
        if lead.status == 'LOST':
            lead.next_followup_date = None
        else:
            next_fup_str = request.POST.get('next_followup_date')
            if next_fup_str:
                try:
                    dt = datetime.fromisoformat(next_fup_str)
                    lead.next_followup_date = timezone.make_aware(dt) if timezone.is_naive(dt) else dt
                except Exception:
                    pass
            elif 'clear_followup' in request.POST:
                lead.next_followup_date = None

        # Reassign staff if admin
        if admin_mode and 'assigned_to' in request.POST:
            assigned_id = request.POST.get('assigned_to')
            if assigned_id:
                lead.assigned_to = User.objects.filter(id=assigned_id, memberships__company=company).first()
            else:
                lead.assigned_to = None

        lead.save()

        AuditEvent.log(
            company=company,
            actor=request.user,
            action='UPDATE',
            module='crm',
            model_name='Lead',
            object_id=str(lead.id),
            object_repr=f"{lead.lead_number} - {lead.company_name}",
            changes={
                'status': lead.status,
                'priority': lead.priority,
                'estimated_value': str(lead.estimated_value),
                'assigned_to': lead.assigned_to.username if lead.assigned_to else "Unassigned"
            }
        )

        messages.success(request, f"Lead {lead.lead_number} updated successfully.")
        return redirect('crm:lead_detail', lead_id=lead.id)

    assignable_users = get_assignable_users(company) if admin_mode else []
    can_delete = can_delete_lead(request.user, membership)

    return render(request, 'crm/lead_form_edit.html', {
        'lead': lead,
        'is_admin': admin_mode,
        'can_delete': can_delete,
        'assignable_users': assignable_users,
        'status_choices': Lead.STATUS_CHOICES,
        'priority_choices': Lead.PRIORITY_CHOICES,
        'active_tab': 'crm',
        'active_sub': 'leads'
    })


@login_required
def lead_delete(request, lead_id):
    """
    Permanently deletes a single lead record. Restricted strictly to Admin or permitted staff.
    """
    company = request.company
    membership = getattr(request, 'membership', None)

    if not can_delete_lead(request.user, membership):
        messages.error(request, "Permission denied: Only administrators or permitted staff can delete leads.")
        return redirect('crm:lead_list')

    lead = get_object_or_404(Lead, id=lead_id, company=company)
    was_lost = (lead.status == 'LOST')
    if request.method == 'POST':
        lead_num = lead.lead_number
        lead_name = lead.company_name or lead.contact_person

        AuditEvent.log(
            company=company,
            actor=request.user,
            action='DELETE',
            module='crm',
            model_name='Lead',
            object_id=str(lead.id),
            object_repr=f"Deleted Lead {lead_num} - {lead_name}",
            changes={'deleted_lead_id': lead.id, 'lead_number': lead_num, 'company_name': lead_name}
        )

        lead.delete()
        messages.success(request, f"Lead {lead_num} ({lead_name}) has been permanently deleted.")

        referer = request.POST.get('next') or request.META.get('HTTP_REFERER') or ''
        # Avoid redirecting back to detail/edit view of the lead that was just deleted
        if f"/leads/{lead_id}/" in referer or f"/leads/{lead_id}" in referer or not referer:
            if was_lost:
                return redirect(f"{reverse('crm:lead_list')}?status=LOST")
            return redirect('crm:lead_list')

        return redirect(referer)

    return redirect('crm:lead_list')


@login_required
@require_permission('crm', 'view')
def lead_detail(request, lead_id):
    """
    Detailed lead view with interaction history, conversion options,
    staff reassignment, follow-up logging, and admin audit & security event stream.
    """
    company = request.company
    lead = get_object_or_404(Lead, id=lead_id, company=company)
    membership = getattr(request, 'membership', None)
    admin_mode = is_crm_admin(request.user, membership)

    if request.method == 'POST':
        summary = request.POST.get('summary', '').strip()
        method = request.POST.get('method', 'PHONE')
        outcome = request.POST.get('outcome', '')
        next_action = request.POST.get('next_action', '')
        status = request.POST.get('status', lead.status)
        
        next_fup_str = request.POST.get('next_followup_date', '').strip()
        next_time_str = request.POST.get('next_followup_time', '').strip()
        next_fup = None
        if status == 'LOST':
            next_fup = None
        elif next_fup_str:
            try:
                if 'T' in next_fup_str:
                    dt = datetime.fromisoformat(next_fup_str)
                else:
                    d = datetime.strptime(next_fup_str, '%Y-%m-%d').date()
                    if next_time_str:
                        t = datetime.strptime(next_time_str, '%H:%M').time()
                    else:
                        t = time(10, 0)
                    dt = datetime.combine(d, t)
                next_fup = timezone.make_aware(dt) if timezone.is_naive(dt) else dt
            except Exception:
                pass

        if summary:
            record_lead_followup(
                lead=lead,
                staff=request.user,
                method=method,
                summary=summary,
                outcome=outcome,
                next_action=next_action,
                next_followup_date=next_fup,
                new_status=status
            )
            messages.success(request, f"Follow-up activity recorded for {lead.company_name}.")
            return redirect(f"{reverse('crm:followup_dashboard')}?tab=today")

    assignable_users = get_assignable_users(company) if admin_mode else []

    # Follow-ups with backend pagination
    followups_qs = lead.followups.all().select_related('staff').order_by('-interaction_time')
    fup_paginator = Paginator(followups_qs, 6)
    fup_page_number = request.GET.get('fup_page', 1)
    try:
        followups_page = fup_paginator.get_page(fup_page_number)
    except Exception:
        followups_page = fup_paginator.get_page(1)

    # Lead-specific Audit & Security Event Stream (Viewable ONLY by Admins with pagination)
    audit_events_page = []
    if admin_mode:
        audit_events = AuditEvent.objects.filter(
            company=company,
            module='crm'
        ).filter(
            Q(model_name__in=['Lead', 'LeadFollowup'], object_id=str(lead.id)) |
            Q(object_repr__icontains=lead.lead_number)
        ).select_related('actor').order_by('-timestamp')
        audit_paginator = Paginator(audit_events, 6)
        audit_page_number = request.GET.get('audit_page', 1)
        try:
            audit_events_page = audit_paginator.get_page(audit_page_number)
        except Exception:
            audit_events_page = audit_paginator.get_page(1)

    now = timezone.localtime(timezone.now())
    tomorrow_date = (now.date() + timedelta(days=1)).strftime('%Y-%m-%d')

    return render(request, 'crm/lead_detail.html', {
        'lead': lead,
        'followups': followups_page,
        'is_admin': admin_mode,
        'can_delete': can_delete_lead(request.user, membership),
        'assignable_users': assignable_users,
        'audit_events': audit_events_page,
        'tomorrow_date': tomorrow_date,
        'status_choices': Lead.STATUS_CHOICES,
        'method_choices': LeadFollowup.METHOD_CHOICES,
        'active_tab': 'crm',
        'active_sub': 'leads'
    })


@login_required
@require_permission('crm', 'edit')
def lead_quick_followup(request, lead_id):
    """
    Fast endpoint to record a follow-up directly from Dashboard/List modals
    """
    company = request.company
    lead = get_object_or_404(Lead, id=lead_id, company=company)

    if request.method == 'POST':
        summary = request.POST.get('summary', '').strip()
        method = request.POST.get('method', 'PHONE')
        outcome = request.POST.get('outcome', '')
        next_action = request.POST.get('next_action', '')
        status = request.POST.get('status', lead.status)
        
        next_fup_str = request.POST.get('next_followup_date', '').strip()
        next_time_str = request.POST.get('next_followup_time', '').strip()
        next_fup = None
        if status == 'LOST':
            next_fup = None
        elif next_fup_str:
            try:
                if 'T' in next_fup_str:
                    dt = datetime.fromisoformat(next_fup_str)
                else:
                    d = datetime.strptime(next_fup_str, '%Y-%m-%d').date()
                    if next_time_str:
                        t = datetime.strptime(next_time_str, '%H:%M').time()
                    else:
                        t = time(10, 0)
                    dt = datetime.combine(d, t)
                next_fup = timezone.make_aware(dt) if timezone.is_naive(dt) else dt
            except Exception:
                pass

        if summary:
            record_lead_followup(
                lead=lead,
                staff=request.user,
                method=method,
                summary=summary,
                outcome=outcome,
                next_action=next_action,
                next_followup_date=next_fup,
                new_status=status
            )
            messages.success(request, f"Follow-up logged for {lead.company_name} ({lead.lead_number}).")
        else:
            messages.error(request, "Discussion notes summary cannot be empty.")

    next_url = request.POST.get('next') or request.META.get('HTTP_REFERER') or f"{reverse('crm:followup_dashboard')}?tab=today"
    return redirect(next_url)


@login_required
@require_permission('crm', 'edit')
def lead_quick_assign(request, lead_id):
    """
    Fast staff reassignment endpoint for admins
    """
    company = request.company
    lead = get_object_or_404(Lead, id=lead_id, company=company)
    membership = getattr(request, 'membership', None)

    if not is_crm_admin(request.user, membership):
        messages.error(request, "You do not have administrative permission to reassign leads.")
        return redirect('crm:lead_detail', lead_id=lead.id)

    if request.method == 'POST':
        assigned_user_id = request.POST.get('assigned_to')
        target_user = None
        if assigned_user_id:
            target_user = User.objects.filter(id=assigned_user_id, memberships__company=company).first()
        
        assign_lead_to_staff(lead, target_user, request.user)
        name = target_user.get_full_name() or target_user.username if target_user else "Unassigned"
        messages.success(request, f"Lead {lead.lead_number} assigned to {name}.")

    next_url = request.META.get('HTTP_REFERER') or 'crm:lead_detail'
    return redirect(next_url)


@login_required
@require_permission('crm', 'edit')
def lead_bulk_action(request):
    """
    Executes bulk actions across selected leads:
    - assign: Reassigns selected leads to chosen staff
    - status: Changes status of selected leads
    - priority: Changes priority of selected leads
    - delete: Deletes selected leads (admins only)
    """
    company = request.company
    membership = getattr(request, 'membership', None)
    admin_mode = is_crm_admin(request.user, membership)

    if request.method == 'POST':
        action = request.POST.get('bulk_action', '')
        lead_ids_raw = request.POST.getlist('selected_lead_ids')
        if not lead_ids_raw:
            lead_ids_str = request.POST.get('lead_ids_str', '')
            if lead_ids_str:
                lead_ids_raw = [x.strip() for x in lead_ids_str.split(',') if x.strip()]

        lead_ids = [int(x) for x in lead_ids_raw if str(x).isdigit()]

        if not lead_ids:
            messages.warning(request, "No leads were selected for the bulk action.")
            return redirect(request.META.get('HTTP_REFERER', 'crm:lead_list'))

        if action == 'assign':
            if not admin_mode:
                messages.error(request, "Only administrators can bulk reassign leads.")
                return redirect(request.META.get('HTTP_REFERER', 'crm:lead_list'))
            
            target_staff_id = request.POST.get('target_staff_id')
            target_user = User.objects.filter(id=target_staff_id, memberships__company=company).first() if target_staff_id else None
            count = bulk_assign_leads(company, lead_ids, target_user, request.user)
            staff_label = (target_user.get_full_name() or target_user.username) if target_user else "Unassigned"
            messages.success(request, f"Successfully reassigned {count} leads to {staff_label}.")

        elif action == 'status':
            target_status = request.POST.get('target_status')
            count = bulk_update_lead_status(company, lead_ids, target_status, request.user)
            messages.success(request, f"Successfully updated status of {count} leads to {target_status}.")

        elif action == 'priority':
            target_priority = request.POST.get('target_priority')
            count = bulk_update_lead_priority(company, lead_ids, target_priority, request.user)
            messages.success(request, f"Successfully updated priority of {count} leads to {target_priority}.")

        elif action == 'delete':
            if not can_delete_lead(request.user, membership):
                messages.error(request, "Permission denied: Only administrators or permitted staff can delete leads in bulk.")
                return redirect(request.META.get('HTTP_REFERER', 'crm:lead_list'))
            count = bulk_delete_leads(company, lead_ids, request.user)
            messages.success(request, f"Successfully deleted {count} leads.")

        else:
            messages.error(request, "Unrecognized bulk action operation.")

    return redirect(request.META.get('HTTP_REFERER', 'crm:lead_list'))


@login_required
@require_permission('crm', 'create')
def lead_bulk_upload(request):
    """
    Bulk Upload / Import View:
    - If Admin: Can assign all to chosen staff, round-robin distribute, or leave unassigned
    - If Staff: Automatically assigns all imported leads to themselves
    """
    company = request.company
    membership = getattr(request, 'membership', None)
    admin_mode = is_crm_admin(request.user, membership)

    if request.method == 'POST':
        file_obj = request.FILES.get('lead_file')
        if not file_obj:
            messages.error(request, "Please select a valid CSV or Excel file to upload.")
            return redirect('crm:lead_bulk_upload')

        # Determine assignment mode
        if not admin_mode:
            # Staff uploads -> always auto-assign to themselves
            assignment_mode = 'self'
            target_user_id = request.user.id
        else:
            assignment_mode = request.POST.get('assignment_mode', 'self')
            target_user_id = request.POST.get('assign_to_staff_id')

        created_count, errors = parse_and_import_leads(
            company=company,
            file_obj=file_obj,
            uploaded_by=request.user,
            assignment_mode=assignment_mode,
            target_user_id=target_user_id
        )

        if created_count > 0:
            messages.success(request, f"Successfully imported and registered {created_count} new leads!")
        
        if errors:
            for err in errors[:5]:
                messages.warning(request, err)
            if len(errors) > 5:
                messages.warning(request, f"...and {len(errors) - 5} more row formatting warnings.")

        return redirect('crm:lead_list')

    assignable_users = get_assignable_users(company) if admin_mode else []

    return render(request, 'crm/lead_bulk_upload.html', {
        'is_admin': admin_mode,
        'assignable_users': assignable_users,
        'active_tab': 'crm',
        'active_sub': 'bulk_upload'
    })


@login_required
@require_permission('crm', 'view')
def lead_template_download(request):
    """
    Generates and downloads a clean CSV template for lead import
    """
    csv_data = generate_sample_leads_csv()
    response = HttpResponse(csv_data, content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="crm_leads_import_template.csv"'
    return response


@login_required
@require_permission('crm', 'view')
def lead_template_excel_download(request):
    """
    Generates and downloads a clean Excel (.xlsx) template for lead import
    """
    excel_data = generate_sample_leads_excel()
    response = HttpResponse(
        excel_data,
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = 'attachment; filename="crm_leads_import_template.xlsx"'
    return response


@login_required
@require_permission('crm', 'view')
def staff_analytics(request):
    """
    Admin Employee Performance & Follow-up Analytics:
    - Shows each employee's metrics: leads followed up, calls made, leads converted, total assigned
    - Date range filtering (Today, Last 7 Days, Month, Year, Custom)
    - Date and performance sorting
    - Backend pagination for high performance
    """
    company = request.company
    membership = getattr(request, 'membership', None)
    admin_mode = is_crm_admin(request.user, membership)

    if not admin_mode:
        messages.error(request, "Access restricted: Staff performance analytics is reserved for company administrators and managers.")
        return redirect('crm:lead_list')

    now = timezone.localtime(timezone.now())
    today = now.date()

    # Parse date presets or custom range
    preset = request.GET.get('preset', 'this_month')
    start_date_str = request.GET.get('start_date', '')
    end_date_str = request.GET.get('end_date', '')
    sort_by = request.GET.get('sort_by', '-followups_count')
    staff_id_str = request.GET.get('staff_id', '')
    staff_id = int(staff_id_str) if staff_id_str and staff_id_str.isdigit() else None

    start_date = None
    end_date = None

    if preset == 'today':
        start_date = today
        end_date = today
    elif preset == 'yesterday':
        start_date = today - timedelta(days=1)
        end_date = today - timedelta(days=1)
    elif preset == 'last_7_days':
        start_date = today - timedelta(days=7)
        end_date = today
    elif preset == 'this_month':
        start_date = today.replace(day=1)
        end_date = today
    elif preset == 'last_month':
        first_of_this_month = today.replace(day=1)
        last_of_last_month = first_of_this_month - timedelta(days=1)
        start_date = last_of_last_month.replace(day=1)
        end_date = last_of_last_month
    elif preset == 'this_year':
        start_date = today.replace(month=1, day=1)
        end_date = today
    elif preset == 'all_time':
        start_date = date(2020, 1, 1)
        end_date = today
    elif preset == 'custom' and start_date_str and end_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except Exception:
            start_date = today.replace(day=1)
            end_date = today
    else:
        start_date = today.replace(day=1)
        end_date = today

    # Fetch performance stats
    performance_data = get_staff_crm_performance(
        company=company,
        start_date=start_date,
        end_date=end_date,
        sort_by=sort_by,
        staff_id=staff_id
    )

    staff_list = performance_data['staff_stats']
    summary = performance_data['summary']

    # Fetch 360-degree analytics metrics for executive dashboard integration
    analytics_360 = get_crm_360_analytics_data(
        company=company,
        start_date=start_date,
        end_date=end_date,
        staff_id=staff_id
    )

    # Active staff members for filter dropdown and reports modal
    all_staff_members = CompanyMembership.objects.filter(
        company=company,
        is_active=True
    ).select_related('user', 'user__employee_profile', 'custom_role')

    # Backend pagination
    paginator = Paginator(staff_list, 10)
    page_number = request.GET.get('page', 1)
    try:
        staff_page = paginator.get_page(page_number)
    except Exception:
        staff_page = paginator.get_page(1)

    return render(request, 'crm/staff_analytics.html', {
        'staff_page': staff_page,
        'summary': summary,
        'analytics_360': analytics_360,
        'all_staff_members': all_staff_members,
        'selected_staff_id': staff_id_str,
        'preset': preset,
        'start_date': start_date.strftime('%Y-%m-%d') if start_date else '',
        'end_date': end_date.strftime('%Y-%m-%d') if end_date else '',
        'sort_by': sort_by,
        'active_tab': 'crm',
        'active_sub': 'staff_analytics'
    })


@login_required
@require_permission('crm', 'view')
def staff_analytics_export(request):
    """
    Exports 360-degree CRM & Staff Analytics reports in Excel (.xlsx) or PDF (.pdf).
    Reserved for company administrators and managers.
    Parameters supported:
    - format: 'excel' or 'pdf'
    - report_type: '360_master', 'staff_performance', 'lead_quality', 'conversion_funnel', 'activity_log'
    - preset / custom start_date & end_date
    - staff_id (optional filter)
    """
    company = request.company
    membership = getattr(request, 'membership', None)
    admin_mode = is_crm_admin(request.user, membership)

    if not admin_mode:
        messages.error(request, "Access restricted: Analytical reports are reserved for administrators and managers.")
        return redirect('crm:staff_analytics')

    now = timezone.localtime(timezone.now())
    today = now.date()

    # Parse parameters
    preset = request.GET.get('preset', 'this_month')
    start_date_str = request.GET.get('start_date', '')
    end_date_str = request.GET.get('end_date', '')
    report_type = request.GET.get('report_type', '360_master')
    export_format = request.GET.get('format', 'excel').lower()
    staff_id_str = request.GET.get('staff_id', '')
    staff_id = int(staff_id_str) if staff_id_str and staff_id_str.isdigit() else None

    start_date = None
    end_date = None

    if preset == 'today':
        start_date = today
        end_date = today
    elif preset == 'yesterday':
        start_date = today - timedelta(days=1)
        end_date = today - timedelta(days=1)
    elif preset == 'last_7_days':
        start_date = today - timedelta(days=7)
        end_date = today
    elif preset == 'this_month':
        start_date = today.replace(day=1)
        end_date = today
    elif preset == 'last_month':
        first_of_this_month = today.replace(day=1)
        last_of_last_month = first_of_this_month - timedelta(days=1)
        start_date = last_of_last_month.replace(day=1)
        end_date = last_of_last_month
    elif preset == 'this_year':
        start_date = today.replace(month=1, day=1)
        end_date = today
    elif preset == 'all_time':
        start_date = date(2020, 1, 1)
        end_date = today
    elif preset == 'custom' and start_date_str and end_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except Exception:
            start_date = today.replace(day=1)
            end_date = today
    else:
        start_date = today.replace(day=1)
        end_date = today

    company_slug = "".join(c if c.isalnum() else "_" for c in company.name)[:25]
    date_stamp = f"{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}"

    if export_format == 'pdf':
        pdf_buf = generate_crm_pdf_report(
            company=company,
            report_type=report_type,
            start_date=start_date,
            end_date=end_date,
            staff_id=staff_id,
            generated_by_user=request.user
        )
        filename = f"ByteBoot_{company_slug}_CRM_{report_type}_{date_stamp}.pdf"
        response = HttpResponse(pdf_buf.getvalue(), content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
    else:
        excel_buf = generate_crm_excel_report(
            company=company,
            report_type=report_type,
            start_date=start_date,
            end_date=end_date,
            staff_id=staff_id
        )
        filename = f"ByteBoot_{company_slug}_CRM_{report_type}_{date_stamp}.xlsx"
        response = HttpResponse(
            excel_buf.getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response


@login_required
@require_permission('crm', 'view')
def lead_ajax_detail(request, lead_id):
    """
    HTMX / Quick View Drawer partial for a lead
    """
    company = request.company
    lead = get_object_or_404(Lead, id=lead_id, company=company)
    membership = getattr(request, 'membership', None)
    admin_mode = is_crm_admin(request.user, membership)

    return render(request, 'crm/lead_drawer_partial.html', {
        'lead': lead,
        'followups': lead.followups.all().select_related('staff')[:10],
        'is_admin': admin_mode,
        'can_delete': can_delete_lead(request.user, membership)
    })


@login_required
@require_permission('crm', 'edit')
def lead_convert(request, lead_id):
    """
    Converts lead into formal customer
    """
    company = request.company
    lead = get_object_or_404(Lead, id=lead_id, company=company)
    
    try:
        customer = convert_lead_to_customer(lead, request.user)
        messages.success(request, f"Lead {lead.lead_number} converted into Customer {customer.customer_code} ({customer.name}).")
        return redirect('crm:customer_detail', customer_id=customer.id)
    except Exception as e:
        messages.error(request, f"Error converting lead: {e}")
        return redirect('crm:lead_detail', lead_id=lead.id)


@login_required
@require_permission('crm', 'view')
def customer_list(request):
    """
    Customer Directory
    """
    company = request.company
    customers = Customer.objects.filter(company=company)
    query = request.GET.get('q', '').strip()
    if query:
        customers = customers.filter(
            Q(name__icontains=query) |
            Q(customer_code__icontains=query) |
            Q(phone__icontains=query) |
            Q(email__icontains=query)
        )

    paginator = Paginator(customers, 15)
    page_number = request.GET.get('page', 1)
    customers_page = paginator.get_page(page_number)

    return render(request, 'crm/customer_list.html', {
        'customers': customers_page,
        'query': query,
        'active_tab': 'crm',
        'active_sub': 'customers'
    })


@login_required
@require_permission('crm', 'view')
def customer_detail(request, customer_id):
    """
    Customer 360 View with paginated invoices and payments
    """
    company = request.company
    customer = get_object_or_404(Customer, id=customer_id, company=company)

    invoices_qs = customer.invoices.all().order_by('-date') if hasattr(customer, 'invoices') else []
    inv_paginator = Paginator(invoices_qs, 8)
    inv_page_number = request.GET.get('inv_page', 1)
    try:
        invoices_page = inv_paginator.get_page(inv_page_number)
    except Exception:
        invoices_page = inv_paginator.get_page(1)

    payments_qs = customer.payments.all().order_by('-payment_date') if hasattr(customer, 'payments') else []
    pay_paginator = Paginator(payments_qs, 8)
    pay_page_number = request.GET.get('pay_page', 1)
    try:
        payments_page = pay_paginator.get_page(pay_page_number)
    except Exception:
        payments_page = pay_paginator.get_page(1)

    return render(request, 'crm/customer_detail.html', {
        'customer': customer,
        'invoices': invoices_page,
        'payments': payments_page,
        'contacts': customer.contacts.all(),
        'active_tab': 'crm',
        'active_sub': 'customers'
    })


@login_required
@require_permission('crm', 'view')
def call_history(request):
    """
    CRM Call & Contacted History, Lead Call Report, and Assigned Calling List:
    - History of calls and contacts made today, tomorrow, yesterday, all-time, or custom date range
    - Date sorting (interaction date, next followup date, company, contact person, phone number)
    - Proper live count metrics for today, tomorrow, yesterday, overdue, and all contacts
    - Admin mode: full visibility across all staff lead call reports with staff breakdown & filter
    - Staff mode: strictly restricted to their respective assigned numbers and lead call list
    - Backend and Frontend pagination with preserved filter query parameters
    - 1-click click-to-call, click-to-WhatsApp, and quick call logger modal
    """
    company = request.company
    membership = getattr(request, 'membership', None)
    admin_mode = is_crm_admin(request.user, membership)

    now = timezone.localtime(timezone.now())
    today = now.date()
    tomorrow = today + timedelta(days=1)
    yesterday = today - timedelta(days=1)

    start_of_today = timezone.make_aware(datetime.combine(today, time.min))
    end_of_today = timezone.make_aware(datetime.combine(today, time.max))

    start_of_tomorrow = timezone.make_aware(datetime.combine(tomorrow, time.min))
    end_of_tomorrow = timezone.make_aware(datetime.combine(tomorrow, time.max))

    start_of_yesterday = timezone.make_aware(datetime.combine(yesterday, time.min))
    end_of_yesterday = timezone.make_aware(datetime.combine(yesterday, time.max))

    # Base querysets
    base_fups = LeadFollowup.objects.filter(lead__company=company)
    base_leads = Lead.objects.filter(company=company)

    # Filter parameters
    staff_id = request.GET.get('staff_id', '').strip()
    tab = request.GET.get('tab', '').strip()
    method = request.GET.get('method', '').strip()
    sort_order = request.GET.get('sort', '-interaction_time').strip()
    query = request.GET.get('q', '').strip()
    start_date_str = request.GET.get('start_date', '').strip()
    end_date_str = request.GET.get('end_date', '').strip()
    page_size_str = request.GET.get('page_size', '15').strip()

    # Staff vs Admin role permissions
    if not admin_mode:
        # Regular staff: strictly restricted to leads assigned to them or followups logged by them
        base_fups = base_fups.filter(
            Q(lead__assigned_to=request.user) | Q(staff=request.user)
        )
        base_leads = base_leads.filter(
            Q(assigned_to=request.user) | Q(created_by=request.user)
        )
        staff_id = ''  # staff cannot filter by other staff
    else:
        # Admin: can filter by specific staff or unassigned
        if staff_id:
            if staff_id == 'unassigned':
                base_fups = base_fups.filter(lead__assigned_to__isnull=True)
                base_leads = base_leads.filter(assigned_to__isnull=True)
            elif staff_id.isdigit():
                sid = int(staff_id)
                base_fups = base_fups.filter(Q(staff_id=sid) | Q(lead__assigned_to_id=sid))
                base_leads = base_leads.filter(assigned_to_id=sid)

    # Calculate live counters with proper counts
    today_calls_count = base_fups.filter(
        interaction_time__range=(start_of_today, end_of_today)
    ).count()

    tomorrow_calls_count = base_leads.filter(
        next_followup_date__range=(start_of_tomorrow, end_of_tomorrow)
    ).exclude(status__in=['WON', 'LOST']).count()

    yesterday_calls_count = base_fups.filter(
        interaction_time__range=(start_of_yesterday, end_of_yesterday)
    ).count()

    overdue_calls_count = base_leads.filter(
        next_followup_date__lt=start_of_today
    ).exclude(status__in=['WON', 'LOST']).count()

    total_calls_count = base_fups.count()

    assigned_leads_count = base_leads.exclude(status__in=['WON', 'LOST']).count()

    phone_calls_count = base_fups.filter(method='PHONE').count()
    whatsapp_count = base_fups.filter(method='WHATSAPP').count()
    email_count = base_fups.filter(method='EMAIL').count()
    meeting_count = base_fups.filter(method__in=['MEETING', 'VIDEO']).count()

    # For Admin: Staff Lead Call Report summary list across all staff
    staff_call_reports = []
    assignable_users = []
    if admin_mode:
        assignable_users = get_assignable_users(company)
        for u in assignable_users:
            u_assigned = Lead.objects.filter(company=company, assigned_to=u).exclude(status__in=['WON', 'LOST']).count()
            u_today = LeadFollowup.objects.filter(lead__company=company, staff=u, interaction_time__range=(start_of_today, end_of_today)).count()
            u_tomorrow = Lead.objects.filter(company=company, assigned_to=u, next_followup_date__range=(start_of_tomorrow, end_of_tomorrow)).exclude(status__in=['WON', 'LOST']).count()
            u_total = LeadFollowup.objects.filter(lead__company=company, staff=u).count()
            u_won = Lead.objects.filter(company=company, assigned_to=u, status='WON').count()
            last_call = LeadFollowup.objects.filter(lead__company=company, staff=u).order_by('-interaction_time').first()

            staff_call_reports.append({
                'user': u,
                'name': u.get_full_name() or u.username,
                'email': u.email,
                'assigned_count': u_assigned,
                'calls_today': u_today,
                'calls_tomorrow': u_tomorrow,
                'total_calls': u_total,
                'won_count': u_won,
                'last_call_time': last_call.interaction_time if last_call else None,
            })

    # Default tab selection
    if not tab:
        if today_calls_count > 0:
            tab = 'today'
        elif tomorrow_calls_count > 0:
            tab = 'tomorrow'
        else:
            tab = 'all'

    is_lead_view = (tab in ('tomorrow', 'overdue', 'assigned'))

    if tab == 'today':
        records_qs = base_fups.filter(interaction_time__range=(start_of_today, end_of_today))
    elif tab == 'yesterday':
        records_qs = base_fups.filter(interaction_time__range=(start_of_yesterday, end_of_yesterday))
    elif tab == 'tomorrow':
        records_qs = base_leads.filter(
            next_followup_date__range=(start_of_tomorrow, end_of_tomorrow)
        ).exclude(status__in=['WON', 'LOST'])
    elif tab == 'overdue':
        records_qs = base_leads.filter(
            next_followup_date__lt=start_of_today
        ).exclude(status__in=['WON', 'LOST'])
    elif tab == 'assigned':
        records_qs = base_leads.exclude(status__in=['WON', 'LOST'])
    else:  # 'all'
        records_qs = base_fups

    # Method filter (applies to followups / calls history)
    if method and not is_lead_view:
        records_qs = records_qs.filter(method=method)

    # Search filter
    if query:
        if is_lead_view:
            records_qs = records_qs.filter(
                Q(company_name__icontains=query) |
                Q(contact_person__icontains=query) |
                Q(phone__icontains=query) |
                Q(email__icontains=query) |
                Q(lead_number__icontains=query) |
                Q(service_interested__icontains=query)
            )
        else:
            records_qs = records_qs.filter(
                Q(lead__company_name__icontains=query) |
                Q(lead__contact_person__icontains=query) |
                Q(lead__phone__icontains=query) |
                Q(lead__email__icontains=query) |
                Q(lead__lead_number__icontains=query) |
                Q(summary__icontains=query) |
                Q(outcome__icontains=query) |
                Q(next_action__icontains=query)
            )

    # Custom Date Range filter
    if start_date_str and end_date_str:
        try:
            sd = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            ed = datetime.strptime(end_date_str, '%Y-%m-%d').date()
            custom_start_date = timezone.make_aware(datetime.combine(sd, time.min))
            custom_end_date = timezone.make_aware(datetime.combine(ed, time.max))
            if is_lead_view:
                records_qs = records_qs.filter(next_followup_date__range=(custom_start_date, custom_end_date))
            else:
                records_qs = records_qs.filter(interaction_time__range=(custom_start_date, custom_end_date))
        except Exception:
            pass

    # Sorting
    if is_lead_view:
        valid_lead_sorts = {
            'next_followup_date': 'next_followup_date',
            '-next_followup_date': '-next_followup_date',
            '-created_at': '-created_at',
            'created_at': 'created_at',
            'company_name': 'company_name',
            'contact_person': 'contact_person',
            'phone': 'phone',
        }
        lead_sort = valid_lead_sorts.get(sort_order, 'next_followup_date')
        if lead_sort == 'next_followup_date':
            records_qs = records_qs.order_by(F('next_followup_date').asc(nulls_last=True), '-created_at')
        elif lead_sort == '-next_followup_date':
            records_qs = records_qs.order_by(F('next_followup_date').desc(nulls_last=True), '-created_at')
        else:
            records_qs = records_qs.order_by(lead_sort)
        records_qs = records_qs.select_related('assigned_to', 'created_by')
    else:
        valid_fup_sorts = {
            '-interaction_time': '-interaction_time',
            'interaction_time': 'interaction_time',
            'next_followup_date': 'next_followup_date',
            '-next_followup_date': '-next_followup_date',
            'company_name': 'lead__company_name',
            'contact_person': 'lead__contact_person',
            'phone': 'lead__phone',
        }
        fup_sort = valid_fup_sorts.get(sort_order, '-interaction_time')
        if fup_sort == 'next_followup_date':
            records_qs = records_qs.order_by(F('next_followup_date').asc(nulls_last=True), '-interaction_time')
        elif fup_sort == '-next_followup_date':
            records_qs = records_qs.order_by(F('next_followup_date').desc(nulls_last=True), '-interaction_time')
        else:
            records_qs = records_qs.order_by(fup_sort)
        records_qs = records_qs.select_related('lead', 'lead__assigned_to', 'staff')

    # Pagination setup
    try:
        page_size = int(page_size_str)
        if page_size not in (10, 15, 25, 50, 100):
            page_size = 15
    except Exception:
        page_size = 15

    paginator = Paginator(records_qs, page_size)
    page_number = request.GET.get('page', 1)
    try:
        records_page = paginator.get_page(page_number)
    except Exception:
        records_page = paginator.get_page(1)

    # Dynamic pagination windowing helper (current page ± 2)
    cur = records_page.number
    total_p = paginator.num_pages
    page_range = [p for p in range(max(1, cur - 2), min(total_p + 1, cur + 3))]

    return render(request, 'crm/call_history.html', {
        'records_page': records_page,
        'is_lead_view': is_lead_view,
        'tab': tab,
        'staff_id': staff_id,
        'method': method,
        'sort_order': sort_order,
        'query': query,
        'start_date': start_date_str,
        'end_date': end_date_str,
        'page_size': page_size,
        'page_range': page_range,
        'is_admin': admin_mode,
        'assignable_users': assignable_users,
        'staff_call_reports': staff_call_reports,
        'today': today,
        'tomorrow_date': tomorrow.strftime('%Y-%m-%d'),
        'today_calls_count': today_calls_count,
        'tomorrow_calls_count': tomorrow_calls_count,
        'yesterday_calls_count': yesterday_calls_count,
        'overdue_calls_count': overdue_calls_count,
        'total_calls_count': total_calls_count,
        'assigned_leads_count': assigned_leads_count,
        'phone_calls_count': phone_calls_count,
        'whatsapp_count': whatsapp_count,
        'email_count': email_count,
        'meeting_count': meeting_count,
        'active_tab': 'crm',
        'active_sub': 'call_history'
    })


@login_required
@require_permission('crm', 'view')
def call_history_export(request):
    """
    Exports filtered Call & Contact History report in Excel (.xlsx) or CSV.
    Respects admin vs staff permissions strictly.
    """
    company = request.company
    membership = getattr(request, 'membership', None)
    admin_mode = is_crm_admin(request.user, membership)

    now = timezone.localtime(timezone.now())
    today = now.date()
    tomorrow = today + timedelta(days=1)
    yesterday = today - timedelta(days=1)

    start_of_today = timezone.make_aware(datetime.combine(today, time.min))
    end_of_today = timezone.make_aware(datetime.combine(today, time.max))
    start_of_tomorrow = timezone.make_aware(datetime.combine(tomorrow, time.min))
    end_of_tomorrow = timezone.make_aware(datetime.combine(tomorrow, time.max))
    start_of_yesterday = timezone.make_aware(datetime.combine(yesterday, time.min))
    end_of_yesterday = timezone.make_aware(datetime.combine(yesterday, time.max))

    base_fups = LeadFollowup.objects.filter(lead__company=company)
    base_leads = Lead.objects.filter(company=company)

    staff_id = request.GET.get('staff_id', '').strip()
    tab = request.GET.get('tab', 'all').strip()
    method = request.GET.get('method', '').strip()
    query = request.GET.get('q', '').strip()

    if not admin_mode:
        base_fups = base_fups.filter(Q(lead__assigned_to=request.user) | Q(staff=request.user))
        base_leads = base_leads.filter(Q(assigned_to=request.user) | Q(created_by=request.user))
    elif staff_id:
        if staff_id == 'unassigned':
            base_fups = base_fups.filter(lead__assigned_to__isnull=True)
            base_leads = base_leads.filter(assigned_to__isnull=True)
        elif staff_id.isdigit():
            sid = int(staff_id)
            base_fups = base_fups.filter(Q(staff_id=sid) | Q(lead__assigned_to_id=sid))
            base_leads = base_leads.filter(assigned_to_id=sid)

    is_lead_view = (tab in ('tomorrow', 'overdue', 'assigned'))

    if tab == 'today':
        records = base_fups.filter(interaction_time__range=(start_of_today, end_of_today)).select_related('lead', 'staff')
    elif tab == 'yesterday':
        records = base_fups.filter(interaction_time__range=(start_of_yesterday, end_of_yesterday)).select_related('lead', 'staff')
    elif tab == 'tomorrow':
        records = base_leads.filter(next_followup_date__range=(start_of_tomorrow, end_of_tomorrow)).exclude(status__in=['WON', 'LOST']).select_related('assigned_to')
    elif tab == 'overdue':
        records = base_leads.filter(next_followup_date__lt=start_of_today).exclude(status__in=['WON', 'LOST']).select_related('assigned_to')
    elif tab == 'assigned':
        records = base_leads.exclude(status__in=['WON', 'LOST']).select_related('assigned_to')
    else:
        records = base_fups.select_related('lead', 'staff')

    if method and not is_lead_view:
        records = records.filter(method=method)

    if query:
        if is_lead_view:
            records = records.filter(
                Q(company_name__icontains=query) | Q(contact_person__icontains=query) | Q(phone__icontains=query)
            )
        else:
            records = records.filter(
                Q(lead__company_name__icontains=query) | Q(lead__contact_person__icontains=query) | Q(lead__phone__icontains=query)
            )

    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Call Report"
    ws.views.sheetView[0].showGridLines = True

    # Title header
    ws.merge_cells('A1:J1')
    title_cell = ws['A1']
    title_cell.value = f"{company.name} - CRM Lead Call & Contact Report"
    title_cell.font = Font(name='Calibri', size=16, bold=True, color='0F172A')

    ws['A2'] = f"Generated On: {now.strftime('%d-%b-%Y %I:%M %p')} | View: {tab.upper()} | Staff: {'All' if not staff_id else staff_id}"
    ws['A2'].font = Font(name='Calibri', size=10, italic=True, color='64748B')

    if is_lead_view:
        headers = [
            'Lead #', 'Company / Client', 'Contact Person', 'Phone Number', 'Email',
            'Assigned Staff', 'Scheduled Reminder', 'Deal Value', 'Priority', 'Pipeline Status'
        ]
    else:
        headers = [
            'Interaction Time', 'Lead #', 'Company / Client', 'Contact Person', 'Phone Number',
            'Staff (Caller)', 'Channel', 'Discussion Notes / Summary', 'Outcome', 'Next Scheduled Call'
        ]

    header_font = Font(name='Calibri', size=11, bold=True, color='FFFFFF')
    header_fill = PatternFill(start_color='1E293B', end_color='1E293B', fill_type='solid')
    thin_border = Border(
        left=Side(style='thin', color='CBD5E1'),
        right=Side(style='thin', color='CBD5E1'),
        top=Side(style='thin', color='CBD5E1'),
        bottom=Side(style='thin', color='CBD5E1')
    )

    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=4, column=col_idx, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        cell.border = thin_border
    ws.row_dimensions[4].height = 26

    for row_idx, item in enumerate(records[:1000], start=5):
        if is_lead_view:
            fup_str = timezone.localtime(item.next_followup_date).strftime('%d-%b-%Y %I:%M %p') if item.next_followup_date else 'Unscheduled'
            row_data = [
                item.lead_number,
                item.company_name,
                item.contact_person,
                item.phone,
                item.email or '-',
                item.assigned_to.get_full_name() or item.assigned_to.username if item.assigned_to else 'Unassigned',
                fup_str,
                float(item.estimated_value),
                item.get_priority_display(),
                item.get_status_display()
            ]
        else:
            fup_str = timezone.localtime(item.next_followup_date).strftime('%d-%b-%Y %I:%M %p') if item.next_followup_date else 'None'
            inter_str = timezone.localtime(item.interaction_time).strftime('%d-%b-%Y %I:%M %p')
            row_data = [
                inter_str,
                item.lead.lead_number,
                item.lead.company_name,
                item.lead.contact_person,
                item.lead.phone,
                item.staff.get_full_name() or item.staff.username,
                item.get_method_display(),
                item.summary,
                item.outcome or '-',
                fup_str
            ]

        fill = PatternFill(start_color='F8FAFC' if row_idx % 2 == 0 else 'FFFFFF', end_color='F8FAFC' if row_idx % 2 == 0 else 'FFFFFF', fill_type='solid')
        for col_idx, val in enumerate(row_data, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.font = Font(name='Calibri', size=10)
            cell.border = thin_border
            cell.fill = fill
            cell.alignment = Alignment(vertical='center')

    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            val_str = str(cell.value or '')
            if len(val_str) > max_len:
                max_len = len(val_str)
        ws.column_dimensions[col_letter].width = min(max(max_len + 3, 12), 40)

    from io import BytesIO
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    filename = f"crm_call_report_{tab}_{today.strftime('%Y%m%d')}.xlsx"
    response = HttpResponse(
        buffer.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response
