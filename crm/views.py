from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from crm.models import Lead, LeadFollowup, Customer, CustomerContact
from crm.services import convert_lead_to_customer
from tenants.decorators import require_permission

@login_required
@require_permission('crm', 'view')
def lead_list(request):
    company = request.company
    status_filter = request.GET.get('status', '')
    query = request.GET.get('q', '')

    leads = Lead.objects.filter(company=company)
    
    if status_filter:
        leads = leads.filter(status=status_filter)
    if query:
        leads = leads.filter(company_name__icontains=query) | leads.filter(contact_person__icontains=query)

    return render(request, 'crm/lead_list.html', {
        'leads': leads,
        'status_filter': status_filter,
        'query': query,
        'active_tab': 'crm',
        'active_sub': 'leads'
    })


@login_required
@require_permission('crm', 'create')
def lead_create(request):
    company = request.company
    if request.method == 'POST':
        count = Lead.objects.filter(company=company).count() + 1
        lead_num = f"LD-{10000 + count}"
        
        lead = Lead.objects.create(
            company=company,
            lead_number=lead_num,
            company_name=request.POST.get('company_name'),
            contact_person=request.POST.get('contact_person'),
            phone=request.POST.get('phone'),
            email=request.POST.get('email', ''),
            address=request.POST.get('address', ''),
            source=request.POST.get('source', 'Website'),
            service_interested=request.POST.get('service_interested', 'Software Development'),
            estimated_value=request.POST.get('estimated_value', '0.00'),
            priority=request.POST.get('priority', 'MEDIUM'),
            status='NEW',
            created_by=request.user,
            assigned_to=request.user
        )
        messages.success(request, f"Lead {lead.lead_number} created successfully.")
        return redirect('crm:lead_detail', lead_id=lead.id)

    return render(request, 'crm/lead_form.html', {
        'active_tab': 'crm',
        'active_sub': 'leads'
    })


@login_required
@require_permission('crm', 'view')
def lead_detail(request, lead_id):
    company = request.company
    lead = get_object_or_404(Lead, id=lead_id, company=company)

    if request.method == 'POST':
        summary = request.POST.get('summary')
        method = request.POST.get('method', 'PHONE')
        outcome = request.POST.get('outcome', '')
        if summary:
            LeadFollowup.objects.create(
                lead=lead,
                staff=request.user,
                method=method,
                summary=summary,
                outcome=outcome
            )
            lead.status = request.POST.get('status', lead.status)
            lead.save()
            messages.success(request, "Follow-up activity recorded.")
            return redirect('crm:lead_detail', lead_id=lead.id)

    return render(request, 'crm/lead_detail.html', {
        'lead': lead,
        'followups': lead.followups.all(),
        'active_tab': 'crm',
        'active_sub': 'leads'
    })


@login_required
@require_permission('crm', 'view')
def lead_ajax_detail(request, lead_id):
    company = request.company
    lead = get_object_or_404(Lead, id=lead_id, company=company)
    return render(request, 'crm/lead_drawer_partial.html', {
        'lead': lead,
        'followups': lead.followups.all()
    })


@login_required
@require_permission('crm', 'edit')
def lead_convert(request, lead_id):
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
    company = request.company
    customers = Customer.objects.filter(company=company)
    return render(request, 'crm/customer_list.html', {
        'customers': customers,
        'active_tab': 'crm',
        'active_sub': 'customers'
    })


@login_required
@require_permission('crm', 'view')
def customer_detail(request, customer_id):
    company = request.company
    customer = get_object_or_404(Customer, id=customer_id, company=company)
    return render(request, 'crm/customer_detail.html', {
        'customer': customer,
        'invoices': customer.invoices.all(),
        'payments': customer.payments.all(),
        'contacts': customer.contacts.all(),
        'active_tab': 'crm',
        'active_sub': 'customers'
    })
