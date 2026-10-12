from datetime import datetime, time, date, timedelta
from decimal import Decimal
import csv
import io
from django.db import transaction
from django.utils import timezone
from django.contrib.auth.models import User
from django.db.models import Count, Q, Max
from crm.models import Lead, LeadFollowup, Customer, CustomerContact
from tenants.models import AuditEvent, CompanyMembership

@transaction.atomic
def convert_lead_to_customer(lead, user):
    """
    Converts a WON lead into a formal Customer record without duplicates.
    Preserves lead history.
    """
    company = lead.company

    # Check for duplicate customer by email or exact name match
    existing_customer = Customer.objects.filter(
        company=company,
        email__iexact=lead.email
    ).first()

    if not existing_customer and lead.company_name:
        existing_customer = Customer.objects.filter(
            company=company,
            name__iexact=lead.company_name
        ).first()

    if existing_customer:
        customer = existing_customer
        if not customer.lead_reference:
            customer.lead_reference = lead
            customer.save(update_fields=['lead_reference'])
    else:
        count = Customer.objects.filter(company=company).count() + 1
        cust_code = f"CUST-{1000 + count}"
        
        customer = Customer.objects.create(
            company=company,
            customer_code=cust_code,
            name=lead.company_name or lead.contact_person,
            company_name=lead.company_name,
            email=lead.email or f"info@{lead.company_name.lower().replace(' ', '')}.com",
            phone=lead.phone,
            billing_address=lead.address or "Address Pending",
            city="Kochi",
            state="Kerala",
            country="India",
            gstin="",
            lead_reference=lead
        )

        # Create primary contact
        CustomerContact.objects.create(
            customer=customer,
            name=lead.contact_person,
            email=lead.email,
            phone=lead.phone,
            designation="Primary Contact",
            is_primary=True
        )

    lead.status = 'WON'
    lead.pipeline_stage = 'Won'
    lead.conversion_probability = 100
    lead.save()

    AuditEvent.log(
        company=company,
        actor=user,
        action='CONVERT',
        module='crm',
        model_name='Lead',
        object_id=lead.id,
        object_repr=str(lead),
        changes={'converted_customer_id': customer.id, 'customer_code': customer.customer_code}
    )

    return customer


@transaction.atomic
def record_lead_followup(lead, staff, method, summary, outcome='', next_action='', next_followup_date=None, new_status=None):
    """
    Records a follow-up activity for a lead and syncs lead dates and status.
    """
    now = timezone.now()
    followup = LeadFollowup.objects.create(
        lead=lead,
        staff=staff,
        method=method,
        summary=summary,
        outcome=outcome,
        next_action=next_action,
        next_followup_date=next_followup_date
    )

    lead.last_followup_date = now
    
    if new_status and new_status in dict(Lead.STATUS_CHOICES):
        lead.status = new_status
        lead.pipeline_stage = dict(Lead.STATUS_CHOICES).get(new_status, new_status)
        if new_status == 'WON':
            lead.conversion_probability = 100
        elif new_status == 'LOST':
            lead.conversion_probability = 0
            next_followup_date = None
        elif new_status == 'QUALIFIED':
            lead.conversion_probability = 60
        elif new_status == 'PROPOSAL_SENT':
            lead.conversion_probability = 80

    if lead.status == 'LOST':
        lead.next_followup_date = None
        followup.next_followup_date = None
        followup.save(update_fields=['next_followup_date'])
    elif next_followup_date:
        lead.next_followup_date = next_followup_date

    lead.save()

    AuditEvent.log(
        company=lead.company,
        actor=staff,
        action='UPDATE',
        module='crm',
        model_name='LeadFollowup',
        object_id=str(lead.id),
        object_repr=f"{lead.lead_number} - Follow-up via {followup.get_method_display()} ({lead.company_name})",
        changes={
            'method': method,
            'summary': summary[:100],
            'outcome': outcome,
            'next_action': next_action,
            'next_followup_date': str(next_followup_date) if next_followup_date else None,
            'status': lead.status
        }
    )

    return followup


def assign_lead_to_staff(lead, assigned_user, actor):
    """
    Reassigns a single lead to a staff member.
    """
    old_assignee = lead.assigned_to.username if lead.assigned_to else "Unassigned"
    lead.assigned_to = assigned_user
    lead.save(update_fields=['assigned_to', 'updated_at'])

    AuditEvent.log(
        company=lead.company,
        actor=actor,
        action='UPDATE',
        module='crm',
        model_name='Lead',
        object_id=lead.id,
        object_repr=str(lead),
        changes={'reassigned_from': old_assignee, 'reassigned_to': assigned_user.username if assigned_user else "Unassigned"}
    )
    return lead


def bulk_assign_leads(company, lead_ids, assigned_user, actor):
    """
    Bulk assigns multiple leads to a staff member.
    """
    leads = Lead.objects.filter(company=company, id__in=lead_ids)
    count = leads.update(assigned_to=assigned_user)

    AuditEvent.log(
        company=company,
        actor=actor,
        action='UPDATE',
        module='crm',
        model_name='Lead',
        object_id='bulk',
        object_repr=f"Bulk assign {count} leads",
        changes={'lead_ids': list(lead_ids), 'assigned_to': assigned_user.username if assigned_user else "Unassigned"}
    )
    return count


def bulk_update_lead_status(company, lead_ids, new_status, actor):
    """
    Bulk updates the status of multiple leads.
    """
    valid_statuses = dict(Lead.STATUS_CHOICES)
    if new_status not in valid_statuses:
        return 0

    stage = valid_statuses[new_status]
    leads = Lead.objects.filter(company=company, id__in=lead_ids)
    if new_status == 'LOST':
        count = leads.update(status=new_status, pipeline_stage=stage, next_followup_date=None, conversion_probability=0)
    else:
        count = leads.update(status=new_status, pipeline_stage=stage)

    AuditEvent.log(
        company=company,
        actor=actor,
        action='UPDATE',
        module='crm',
        model_name='Lead',
        object_id='bulk',
        object_repr=f"Bulk update {count} leads status to {new_status}",
        changes={'lead_ids': list(lead_ids), 'status': new_status}
    )
    return count


def bulk_update_lead_priority(company, lead_ids, new_priority, actor):
    """
    Bulk updates the priority of multiple leads.
    """
    valid_priorities = dict(Lead.PRIORITY_CHOICES)
    if new_priority not in valid_priorities:
        return 0

    leads = Lead.objects.filter(company=company, id__in=lead_ids)
    count = leads.update(priority=new_priority)

    AuditEvent.log(
        company=company,
        actor=actor,
        action='UPDATE',
        module='crm',
        model_name='Lead',
        object_id='bulk',
        object_repr=f"Bulk update {count} leads priority to {new_priority}",
        changes={'lead_ids': list(lead_ids), 'priority': new_priority}
    )
    return count


def bulk_delete_leads(company, lead_ids, actor):
    """
    Bulk deletes multiple leads.
    """
    leads = Lead.objects.filter(company=company, id__in=lead_ids)
    count = leads.count()
    leads.delete()

    AuditEvent.log(
        company=company,
        actor=actor,
        action='DELETE',
        module='crm',
        model_name='Lead',
        object_id='bulk',
        object_repr=f"Bulk deleted {count} leads",
        changes={'lead_ids': list(lead_ids)}
    )
    return count


def generate_sample_leads_csv():
    """
    Generates a sample CSV template for bulk uploading leads.
    """
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Company Name",
        "Contact Person",
        "Phone",
        "Email",
        "Service Interested",
        "Estimated Value",
        "Priority",
        "Lead Source",
        "Address",
        "Notes",
        "Next Followup Date"
    ])
    writer.writerow([
        "Apex Digital Media",
        "Rahul Sharma",
        "+91 9876543210",
        "rahul@apexdigital.in",
        "Custom ERP & Cloud Solution",
        "250000.00",
        "HIGH",
        "Website",
        "MG Road, Kochi",
        "Looking for customized business automation",
        (timezone.now() + timedelta(days=2)).strftime("%Y-%m-%d 11:00")
    ])
    writer.writerow([
        "Green Valley Exports",
        "Ananya Nair",
        "+91 9845612345",
        "ananya@greenvalley.com",
        "Mobile App Development",
        "180000.00",
        "MEDIUM",
        "Referral",
        "Infopark Phase 2, Kochi",
        "Referred by policybazar client",
        (timezone.now() + timedelta(days=1)).strftime("%Y-%m-%d 15:30")
    ])
    return output.getvalue()


def parse_and_import_leads(company, file_obj, uploaded_by, assignment_mode='self', target_user_id=None):
    """
    Parses and imports leads from CSV or XLSX file.
    assignment_mode:
      - 'self': auto-assigns to uploaded_by (used when staff uploads)
      - 'specific': assigns to User with ID target_user_id
      - 'round_robin': evenly distributes across active staff in company
      - 'unassigned': sets assigned_to to None
    """
    filename = file_obj.name.lower()
    rows = []

    if filename.endswith('.csv'):
        content = file_obj.read().decode('utf-8-sig', errors='replace')
        reader = csv.reader(io.StringIO(content))
        header = None
        for r in reader:
            if not r or not any(field.strip() for field in r):
                continue
            if header is None:
                header = [h.strip().lower() for h in r]
            else:
                row_dict = {}
                for idx, col_name in enumerate(header):
                    row_dict[col_name] = r[idx].strip() if idx < len(r) else ''
                rows.append(row_dict)
    elif filename.endswith('.xlsx') or filename.endswith('.xls'):
        import openpyxl
        wb = openpyxl.load_workbook(file_obj, data_only=True)
        sheet = wb.active
        header = None
        for r in sheet.iter_rows(values_only=True):
            if not r or not any(r):
                continue
            if header is None:
                header = [str(h or '').strip().lower() for h in r]
            else:
                row_dict = {}
                for idx, col_name in enumerate(header):
                    val = r[idx] if idx < len(r) else ''
                    row_dict[col_name] = str(val or '').strip()
                rows.append(row_dict)
    else:
        return 0, ["Unsupported file format. Please upload a .csv or .xlsx spreadsheet file."]

    if not rows:
        return 0, ["The uploaded spreadsheet file has no rows to import."]

    # Resolve assignees
    company_staff_users = list(User.objects.filter(
        memberships__company=company,
        memberships__is_active=True
    ).distinct().order_by('username'))

    specific_user = None
    if assignment_mode == 'specific' and target_user_id:
        specific_user = User.objects.filter(id=target_user_id, memberships__company=company).first()

    created_count = 0
    errors = []
    
    current_max_count = Lead.objects.filter(company=company).count()

    def get_val(row_dict, aliases, default=''):
        for alias in aliases:
            for k in row_dict:
                if alias in k:
                    v = row_dict[k]
                    if v:
                        return v
        return default

    round_robin_idx = 0

    with transaction.atomic():
        for line_num, row in enumerate(rows, start=2):
            company_name = get_val(row, ['company name', 'company', 'client name', 'client'])
            contact_person = get_val(row, ['contact person', 'contact', 'person', 'name'])
            phone = get_val(row, ['phone', 'mobile', 'cell', 'tel'])
            email = get_val(row, ['email', 'mail'])
            service = get_val(row, ['service interested', 'service', 'product', 'requirement'], 'Software Development')
            value_str = get_val(row, ['estimated value', 'estimated', 'value', 'amount', 'budget'], '0.00')
            priority = get_val(row, ['priority'], 'MEDIUM').upper()
            source = get_val(row, ['lead source', 'source'], 'Website')
            address = get_val(row, ['address', 'location'], '')
            notes = get_val(row, ['notes', 'note', 'remark', 'remarks'], '')
            fup_date_str = get_val(row, ['next followup date', 'followup date', 'followup', 'follow up', 'next follow'], '')

            if not company_name and not contact_person:
                errors.append(f"Row {line_num}: Missing both Company Name and Contact Person. Skipped.")
                continue

            if not phone:
                phone = "Not Provided"

            # Parse estimated value
            try:
                # clean currency symbols
                clean_val = value_str.replace('₹', '').replace('$', '').replace(',', '').strip()
                estimated_val = Decimal(clean_val) if clean_val else Decimal('0.00')
            except Exception:
                estimated_val = Decimal('0.00')

            # Normalize priority
            if priority not in ('LOW', 'MEDIUM', 'HIGH', 'URGENT'):
                priority = 'MEDIUM'

            # Parse next followup date
            next_fup = None
            if fup_date_str:
                for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d', '%d/%m/%Y %H:%M', '%d/%m/%Y', '%d-%m-%Y %H:%M', '%d-%m-%Y'):
                    try:
                        parsed_dt = datetime.strptime(fup_date_str, fmt)
                        if timezone.is_naive(parsed_dt):
                            next_fup = timezone.make_aware(parsed_dt)
                        else:
                            next_fup = parsed_dt
                        break
                    except (ValueError, TypeError):
                        pass

            # Determine assignment for this row
            row_assigned_to = None
            if assignment_mode == 'self':
                row_assigned_to = uploaded_by
            elif assignment_mode == 'specific':
                row_assigned_to = specific_user or uploaded_by
            elif assignment_mode == 'round_robin':
                if company_staff_users:
                    row_assigned_to = company_staff_users[round_robin_idx % len(company_staff_users)]
                    round_robin_idx += 1
                else:
                    row_assigned_to = uploaded_by
            elif assignment_mode == 'unassigned':
                row_assigned_to = None
            else:
                row_assigned_to = uploaded_by

            current_max_count += 1
            lead_num = f"LD-{10000 + current_max_count}"

            # Check if lead number already taken, find next safe
            while Lead.objects.filter(company=company, lead_number=lead_num).exists():
                current_max_count += 1
                lead_num = f"LD-{10000 + current_max_count}"

            lead = Lead.objects.create(
                company=company,
                lead_number=lead_num,
                company_name=company_name or contact_person,
                contact_person=contact_person or company_name,
                phone=phone,
                email=email,
                address=address,
                source=source,
                service_interested=service,
                estimated_value=estimated_val,
                priority=priority,
                status='NEW',
                pipeline_stage='New',
                notes=notes,
                next_followup_date=next_fup,
                created_by=uploaded_by,
                assigned_to=row_assigned_to
            )
            created_count += 1

    AuditEvent.log(
        company=company,
        actor=uploaded_by,
        action='CREATE',
        module='crm',
        model_name='Lead',
        object_id='bulk_upload',
        object_repr=f"Imported {created_count} leads via file {file_obj.name}",
        changes={'imported_count': created_count, 'assignment_mode': assignment_mode}
    )

    return created_count, errors


def get_staff_crm_performance(company, start_date=None, end_date=None, sort_by='-followups_count'):
    """
    Aggregates performance analytics for each staff member in the company:
    - Followups logged in date range
    - Calls made (PHONE)
    - Converted leads / Won leads
    - Total assigned leads
    - Overdue followups pending
    """
    now = timezone.localtime(timezone.now())
    today = now.date()
    start_of_today = timezone.make_aware(datetime.combine(today, time.min))

    # Resolve date boundaries
    if not start_date:
        # Default to first day of current month
        start_date = today.replace(day=1)
    if not end_date:
        end_date = today

    range_start = timezone.make_aware(datetime.combine(start_date, time.min))
    range_end = timezone.make_aware(datetime.combine(end_date, time.max))

    # Get all company members
    memberships = CompanyMembership.objects.filter(
        company=company,
        is_active=True
    ).select_related('user', 'user__employee_profile', 'custom_role')

    staff_stats = []
    
    total_company_followups = 0
    total_company_calls = 0
    total_company_converted = 0

    for m in memberships:
        user = m.user
        emp = getattr(user, 'employee_profile', None)
        full_name = emp.full_name if emp else (user.get_full_name() or user.username)
        designation = emp.designation if emp else (m.custom_role.name if m.custom_role else m.get_role_display())

        # Followups in period by this staff
        user_followups = LeadFollowup.objects.filter(
            lead__company=company,
            staff=user,
            interaction_time__range=(range_start, range_end)
        )
        followups_count = user_followups.count()
        calls_count = user_followups.filter(method='PHONE').count()
        whatsapp_count = user_followups.filter(method='WHATSAPP').count()
        meetings_count = user_followups.filter(method='MEETING').count()
        emails_count = user_followups.filter(method='EMAIL').count()

        # Assigned leads
        assigned_leads = Lead.objects.filter(company=company, assigned_to=user)
        total_assigned = assigned_leads.count()

        # Won/Converted leads in period
        converted_count = assigned_leads.filter(status='WON', updated_at__range=(range_start, range_end)).count()
        lost_count = assigned_leads.filter(status='LOST', updated_at__range=(range_start, range_end)).count()

        # Overdue followups pending
        overdue_count = assigned_leads.filter(
            next_followup_date__lt=start_of_today
        ).exclude(status__in=['WON', 'LOST']).count()

        # Today followups
        today_fup_count = assigned_leads.filter(
            next_followup_date__range=(start_of_today, timezone.make_aware(datetime.combine(today, time.max)))
        ).exclude(status__in=['WON', 'LOST']).count()

        # Last activity
        last_activity = user_followups.order_by('-interaction_time').first()
        last_activity_time = last_activity.interaction_time if last_activity else None

        conversion_rate = round((converted_count / total_assigned * 100), 1) if total_assigned > 0 else 0.0

        total_company_followups += followups_count
        total_company_calls += calls_count
        total_company_converted += converted_count

        staff_stats.append({
            'user': user,
            'full_name': full_name,
            'username': user.username,
            'email': user.email,
            'designation': designation,
            'role': m.role,
            'followups_count': followups_count,
            'calls_count': calls_count,
            'whatsapp_count': whatsapp_count,
            'meetings_count': meetings_count,
            'emails_count': emails_count,
            'total_assigned': total_assigned,
            'converted_count': converted_count,
            'lost_count': lost_count,
            'overdue_count': overdue_count,
            'today_fup_count': today_fup_count,
            'last_activity_time': last_activity_time,
            'conversion_rate': conversion_rate
        })

    # Sort stats
    reverse = not sort_by.startswith('+')
    sort_key = sort_by.lstrip('+-')
    if sort_key == 'name':
        staff_stats.sort(key=lambda s: s['full_name'].lower(), reverse=reverse)
    elif sort_key in ('followups_count', 'calls_count', 'converted_count', 'total_assigned', 'conversion_rate', 'overdue_count'):
        staff_stats.sort(key=lambda s: s.get(sort_key, 0), reverse=reverse)
    else:
        staff_stats.sort(key=lambda s: s['followups_count'], reverse=True)

    top_performer = max(staff_stats, key=lambda s: s['followups_count']) if staff_stats and staff_stats[0]['followups_count'] > 0 else None

    return {
        'staff_stats': staff_stats,
        'summary': {
            'total_followups': total_company_followups,
            'total_calls': total_company_calls,
            'total_converted': total_company_converted,
            'total_staff': len(staff_stats),
            'top_performer': top_performer,
            'start_date': start_date,
            'end_date': end_date
        }
    }
