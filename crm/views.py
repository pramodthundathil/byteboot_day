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
from django.db.models import Q

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
    parse_and_import_leads,
    get_staff_crm_performance
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
    sort_order = request.GET.get('sort', '-created_at')

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
    if is_lost_view:
        leads = leads.filter(status='LOST')
        active_sub = 'lost_leads'
    elif status_filter:
        leads = leads.filter(status=status_filter)
        active_sub = 'leads'
    else:
        # Main list strictly excludes LOST leads
        leads = leads.exclude(status='LOST')
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

    # Sorting
    valid_sorts = {
        '-created_at': '-created_at',
        'created_at': 'created_at',
        'next_followup_date': 'next_followup_date',
        '-next_followup_date': '-next_followup_date',
        '-estimated_value': '-estimated_value',
        'estimated_value': 'estimated_value',
        'company_name': 'company_name'
    }
    order_by_field = valid_sorts.get(sort_order, '-created_at')
    leads = leads.order_by(order_by_field).select_related('assigned_to', 'created_by')

    # Total counts for status tabs
    all_company_leads = Lead.objects.filter(company=company)
    total_leads_count = all_company_leads.count()
    active_leads_count = all_company_leads.exclude(status='LOST').count()
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
        'priority_filter': priority_filter,
        'staff_filter': staff_filter,
        'fup_filter': fup_filter,
        'query': query,
        'sort_order': sort_order,
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

    return render(request, 'crm/lead_form_edit.html', {
        'lead': lead,
        'is_admin': admin_mode,
        'assignable_users': assignable_users,
        'status_choices': Lead.STATUS_CHOICES,
        'priority_choices': Lead.PRIORITY_CHOICES,
        'active_tab': 'crm',
        'active_sub': 'leads'
    })


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

    return redirect(f"{reverse('crm:followup_dashboard')}?tab=today")


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
            if not admin_mode:
                messages.error(request, "Only administrators can delete leads in bulk.")
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
        sort_by=sort_by
    )

    staff_list = performance_data['staff_stats']
    summary = performance_data['summary']

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
        'preset': preset,
        'start_date': start_date.strftime('%Y-%m-%d') if start_date else '',
        'end_date': end_date.strftime('%Y-%m-%d') if end_date else '',
        'sort_by': sort_by,
        'active_tab': 'crm',
        'active_sub': 'staff_analytics'
    })


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
        'is_admin': admin_mode
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
