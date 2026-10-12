from django.db import transaction
from crm.models import Lead, Customer, CustomerContact
from tenants.models import AuditEvent

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
