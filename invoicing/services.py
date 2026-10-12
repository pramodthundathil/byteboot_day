from decimal import Decimal
from django.db import transaction
from invoicing.models import Invoice, CustomerPayment, PaymentAllocation
from accounting.services import post_payment_to_ledger
from tenants.models import AuditEvent

@transaction.atomic
def process_and_allocate_payment(payment, invoice, amount, user):
    """
    Allocates a customer payment against a target invoice and updates status.
    """
    if payment.status == 'DRAFT':
        post_payment_to_ledger(payment, user)

    if amount > payment.unallocated_amount:
        raise ValueError(f"Cannot allocate ₹{amount}. Only ₹{payment.unallocated_amount} is available in payment.")

    if amount > invoice.balance_due:
        raise ValueError(f"Cannot allocate ₹{amount}. Invoice balance due is only ₹{invoice.balance_due}.")

    allocation = PaymentAllocation.objects.create(
        payment=payment,
        invoice=invoice,
        allocated_amount=amount
    )

    payment.unallocated_amount -= amount
    payment.save(update_fields=['unallocated_amount'])

    invoice.paid_amount += amount
    invoice.balance_due = invoice.grand_total - invoice.paid_amount
    
    if invoice.balance_due <= Decimal('0.00'):
        invoice.status = 'PAID'
    elif invoice.paid_amount > Decimal('0.00'):
        invoice.status = 'PARTIALLY_PAID'

    invoice.save(update_fields=['paid_amount', 'balance_due', 'status'])

    AuditEvent.log(
        company=payment.company,
        actor=user,
        action='UPDATE',
        module='invoicing',
        model_name='CustomerPayment',
        object_id=payment.id,
        object_repr=f"Payment Allocation to {invoice.invoice_number}",
        changes={'allocated_amount': str(amount), 'invoice_balance_due': str(invoice.balance_due)}
    )

    return allocation
