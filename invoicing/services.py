from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from invoicing.models import (
    Invoice, CustomerPayment, PaymentAllocation,
    VendorBill, PaymentMade, BillPaymentAllocation
)
from accounting.models import JournalEntry, JournalLine, Voucher, VoucherLine, BankTransaction
from accounting.services import post_payment_to_ledger, post_journal_entry, get_account_by_code
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


@transaction.atomic
def post_bill_to_ledger(bill, user):
    """
    Posts vendor bill (purchase invoice) to general ledger.
    Debit Expense Account (5030 / 5000 / line items) = subtotal
    Debit Input Tax Credit (1600) = tax_total
    Credit Accounts Payable (2010) = grand_total
    """
    if bill.status not in ('DRAFT', 'POSTED'):
        raise ValueError(f"Bill {bill.bill_number} cannot be posted in state {bill.status}")

    company = bill.company
    ap_account = bill.vendor.gl_account or get_account_by_code(company, '2010')
    default_expense = bill.expense_account or get_account_by_code(company, '5030')
    input_tax_account = get_account_by_code(company, '1600')

    # Create Journal Entry
    je = JournalEntry.objects.create(
        company=company,
        entry_number=f"JE-BILL-{bill.bill_number}",
        date=bill.bill_date,
        reference=bill.vendor_invoice_number or bill.bill_number,
        description=f"Vendor Bill #{bill.bill_number} from {bill.vendor.name}",
        source_module='PURCHASE',
        source_id=str(bill.id)
    )

    # Debit Expense Lines
    tax_total = bill.cgst_total + bill.sgst_total + bill.igst_total
    
    # Check if lines have specific expense accounts
    lines = bill.lines.all()
    if lines.exists():
        for line in lines:
            exp_acc = line.expense_account or default_expense
            JournalLine.objects.create(
                journal=je,
                account=exp_acc,
                description=f"{line.item_name or line.description} - Bill #{bill.bill_number}",
                debit=line.taxable_amount,
                credit=Decimal('0.00'),
                party_type='Vendor',
                party_id=str(bill.vendor.id)
            )
    else:
        JournalLine.objects.create(
            journal=je,
            account=default_expense,
            description=f"Purchase Expense - Bill #{bill.bill_number}",
            debit=bill.subtotal,
            credit=Decimal('0.00'),
            party_type='Vendor',
            party_id=str(bill.vendor.id)
        )

    # Debit Input Tax Credit (GST)
    if tax_total > Decimal('0.00'):
        JournalLine.objects.create(
            journal=je,
            account=input_tax_account,
            description=f"Input Tax Credit (GST) - Bill #{bill.bill_number}",
            debit=tax_total,
            credit=Decimal('0.00')
        )

    # Credit Accounts Payable = Grand Total
    JournalLine.objects.create(
        journal=je,
        account=ap_account,
        description=f"AP - {bill.vendor.name}",
        debit=Decimal('0.00'),
        credit=bill.grand_total,
        party_type='Vendor',
        party_id=str(bill.vendor.id)
    )

    post_journal_entry(je, user)

    # Also create a Purchase Voucher record
    v_count = Voucher.objects.filter(company=company, voucher_type='PURCHASE').count() + 1
    date_str = bill.bill_date.strftime('%Y%m%d')
    voucher_number = f"PV-{date_str}-{v_count:04d}"

    voucher = Voucher.objects.create(
        company=company,
        voucher_number=voucher_number,
        date=bill.bill_date,
        voucher_type='PURCHASE',
        narration=f"Vendor Bill {bill.bill_number} from {bill.vendor.name}: {bill.notes or 'Operating purchase'}",
        reference_number=bill.vendor_invoice_number or bill.bill_number,
        party_type='VENDOR',
        party_id=str(bill.vendor.id),
        party_name=bill.vendor.name,
        total_amount=bill.grand_total,
        status='POSTED',
        journal_entry=je,
        posted_at=timezone.now(),
        posted_by=user
    )

    bill.status = 'POSTED'
    bill.balance_due = bill.grand_total - bill.paid_amount
    bill.posted_journal = je
    bill.voucher = voucher
    bill.save()

    AuditEvent.log(
        company=company,
        actor=user,
        action='POST',
        module='invoicing',
        model_name='VendorBill',
        object_id=bill.id,
        object_repr=f"Posted Bill {bill.bill_number} for {bill.vendor.name}",
        changes={'grand_total': str(bill.grand_total), 'balance_due': str(bill.balance_due)}
    )

    return bill


@transaction.atomic
def post_payment_made_to_ledger(payment_made, user):
    """
    Posts vendor payment to general ledger.
    Debit Accounts Payable (2010)
    Credit Bank Account (1010) / Petty Cash (1020)
    Deducts balance from BankAccount and registers BankTransaction.
    """
    if payment_made.status == 'POSTED':
        raise ValueError("Payment Made is already posted.")

    company = payment_made.company
    ap_account = payment_made.vendor.gl_account or get_account_by_code(company, '2010')

    # Determine bank/cash account
    if payment_made.paid_from_bank and payment_made.paid_from_bank.gl_account:
        bank_gl = payment_made.paid_from_bank.gl_account
    elif payment_made.payment_method == 'CASH':
        bank_gl = get_account_by_code(company, '1020')
    else:
        bank_gl = get_account_by_code(company, '1010')

    # 1. Journal Entry
    je = JournalEntry.objects.create(
        company=company,
        entry_number=f"JE-PMT-{payment_made.payment_number}",
        date=payment_made.payment_date,
        reference=payment_made.reference_number or payment_made.payment_number,
        description=f"Disbursement to {payment_made.vendor.name} via {payment_made.get_payment_method_display()}",
        source_module='PAYMENT_MADE',
        source_id=str(payment_made.id)
    )

    # Debit Accounts Payable (reduces liability)
    JournalLine.objects.create(
        journal=je,
        account=ap_account,
        description=f"Payment to Vendor: {payment_made.vendor.name}",
        debit=payment_made.amount,
        credit=Decimal('0.00'),
        party_type='Vendor',
        party_id=str(payment_made.vendor.id)
    )

    # Credit Bank / Cash (reduces asset)
    JournalLine.objects.create(
        journal=je,
        account=bank_gl,
        description=f"Disbursed from {bank_gl.name}",
        debit=Decimal('0.00'),
        credit=payment_made.amount
    )

    post_journal_entry(je, user)

    # 2. Payment Voucher
    v_count = Voucher.objects.filter(company=company, voucher_type='PAYMENT').count() + 1
    date_str = payment_made.payment_date.strftime('%Y%m%d')
    voucher_number = f"PMT-{date_str}-{v_count:04d}"

    voucher = Voucher.objects.create(
        company=company,
        voucher_number=voucher_number,
        date=payment_made.payment_date,
        voucher_type='PAYMENT',
        narration=f"Disbursement to {payment_made.vendor.name} ref: {payment_made.reference_number or 'Direct'} ({payment_made.notes or ''})",
        reference_number=payment_made.reference_number or payment_made.payment_number,
        party_type='VENDOR',
        party_id=str(payment_made.vendor.id),
        party_name=payment_made.vendor.name,
        total_amount=payment_made.amount,
        status='POSTED',
        journal_entry=je,
        posted_at=timezone.now(),
        posted_by=user
    )

    # 3. Update Bank Account Balance & Record BankTransaction
    if payment_made.paid_from_bank:
        bank = payment_made.paid_from_bank
        bank.current_balance -= payment_made.amount
        bank.save(update_fields=['current_balance'])

        BankTransaction.objects.create(
            bank_account=bank,
            date=payment_made.payment_date,
            transaction_type='WITHDRAWAL',
            amount=payment_made.amount,
            reference=payment_made.reference_number or payment_made.payment_number,
            description=f"Vendor Payment to {payment_made.vendor.name}",
            party_name=payment_made.vendor.name,
            voucher=voucher,
            status='CLEARED',
            running_balance=bank.current_balance
        )

    payment_made.status = 'POSTED'
    payment_made.unallocated_amount = payment_made.amount
    payment_made.posted_journal = je
    payment_made.voucher = voucher
    payment_made.save()

    AuditEvent.log(
        company=company,
        actor=user,
        action='POST',
        module='invoicing',
        model_name='PaymentMade',
        object_id=payment_made.id,
        object_repr=f"Disbursed {payment_made.payment_number} to {payment_made.vendor.name}",
        changes={'amount': str(payment_made.amount)}
    )

    return payment_made


@transaction.atomic
def process_and_allocate_vendor_payment(payment_made, bill, amount, user):
    """
    Allocates an outgoing vendor payment against a target vendor bill and updates status.
    """
    if payment_made.status == 'DRAFT':
        post_payment_made_to_ledger(payment_made, user)

    if amount > payment_made.unallocated_amount:
        raise ValueError(f"Cannot allocate ₹{amount}. Only ₹{payment_made.unallocated_amount} is available in this payment.")

    if amount > bill.balance_due:
        raise ValueError(f"Cannot allocate ₹{amount}. Bill balance due is only ₹{bill.balance_due}.")

    allocation = BillPaymentAllocation.objects.create(
        payment=payment_made,
        bill=bill,
        allocated_amount=amount
    )

    payment_made.unallocated_amount -= amount
    payment_made.save(update_fields=['unallocated_amount'])

    bill.paid_amount += amount
    bill.balance_due = bill.grand_total - bill.paid_amount

    if bill.balance_due <= Decimal('0.00'):
        bill.status = 'PAID'
    elif bill.paid_amount > Decimal('0.00'):
        bill.status = 'PARTIALLY_PAID'

    bill.save(update_fields=['paid_amount', 'balance_due', 'status'])

    AuditEvent.log(
        company=payment_made.company,
        actor=user,
        action='UPDATE',
        module='invoicing',
        model_name='PaymentMade',
        object_id=payment_made.id,
        object_repr=f"Allocated ₹{amount} to Bill {bill.bill_number}",
        changes={'allocated_amount': str(amount), 'bill_balance_due': str(bill.balance_due)}
    )

    return allocation


@transaction.atomic
def auto_allocate_vendor_payment_fifo(payment_made, user):
    """
    Automatically allocates an outgoing payment against open vendor bills in FIFO order.
    """
    if payment_made.status == 'DRAFT':
        post_payment_made_to_ledger(payment_made, user)

    open_bills = VendorBill.objects.filter(
        company=payment_made.company,
        vendor=payment_made.vendor,
        status__in=['POSTED', 'PARTIALLY_PAID']
    ).order_by('bill_date')

    remaining = payment_made.unallocated_amount

    for bill in open_bills:
        if remaining <= Decimal('0.00'):
            break
        due = bill.balance_due
        alloc = min(due, remaining)
        if alloc > Decimal('0.00'):
            process_and_allocate_vendor_payment(payment_made, bill, alloc, user)
            remaining -= alloc

    return payment_made
