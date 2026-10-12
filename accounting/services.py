from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from accounting.models import Account, JournalEntry, JournalLine, AccountingPeriod
from tenants.models import AuditEvent

DEFAULT_ACCOUNTS = [
    # ASSETS
    {'code': '1010', 'name': 'HDFC Main Bank Account', 'type': 'ASSET', 'is_system': True},
    {'code': '1020', 'name': 'Petty Cash Account', 'type': 'ASSET', 'is_system': True},
    {'code': '1100', 'name': 'Undeposited Funds / Cash Clearing', 'type': 'ASSET', 'is_system': True},
    {'code': '1200', 'name': 'Accounts Receivable (Customers)', 'type': 'ASSET', 'is_system': True},
    {'code': '1400', 'name': 'Inventory & Hardware Stock', 'type': 'ASSET', 'is_system': True},
    {'code': '1500', 'name': 'IT & Office Equipment (Fixed Asset)', 'type': 'ASSET', 'is_system': True},
    {'code': '1550', 'name': 'Accumulated Depreciation - Fixed Assets', 'type': 'ASSET', 'is_system': True},
    
    # LIABILITIES
    {'code': '2010', 'name': 'Accounts Payable (Vendors)', 'type': 'LIABILITY', 'is_system': True},
    {'code': '2110', 'name': 'CGST Payable Account', 'type': 'LIABILITY', 'is_system': True},
    {'code': '2120', 'name': 'SGST Payable Account', 'type': 'LIABILITY', 'is_system': True},
    {'code': '2130', 'name': 'IGST Payable Account', 'type': 'LIABILITY', 'is_system': True},
    {'code': '2200', 'name': 'Salaries & Wages Payable', 'type': 'LIABILITY', 'is_system': True},
    {'code': '2300', 'name': 'TDS Payable / Statutory Dues', 'type': 'LIABILITY', 'is_system': True},
    
    # EQUITY
    {'code': '3010', 'name': 'Owner Equity / Share Capital', 'type': 'EQUITY', 'is_system': True},
    {'code': '3020', 'name': 'Retained Earnings', 'type': 'EQUITY', 'is_system': True},
    {'code': '3030', 'name': 'Owner Drawings / Capital Withdrawals', 'type': 'EQUITY', 'is_system': True},
    
    # INCOME
    {'code': '4000', 'name': 'Software Development & Consulting Sales', 'type': 'INCOME', 'is_system': True},
    {'code': '4100', 'name': 'SaaS Subscriptions & License Revenue', 'type': 'INCOME', 'is_system': True},
    {'code': '4200', 'name': 'Other Operating & Interest Income', 'type': 'INCOME', 'is_system': True},
    
    # EXPENSES
    {'code': '5010', 'name': 'Employee Salaries & Staff Benefits', 'type': 'EXPENSE', 'is_system': True},
    {'code': '5020', 'name': 'Office Rent & Workspace Expense', 'type': 'EXPENSE', 'is_system': True},
    {'code': '5030', 'name': 'Cloud Infrastructure & Software Tools', 'type': 'EXPENSE', 'is_system': True},
    {'code': '5040', 'name': 'Marketing, Branding & Customer Acquisition', 'type': 'EXPENSE', 'is_system': True},
    {'code': '5050', 'name': 'Professional, Legal & Audit Fees', 'type': 'EXPENSE', 'is_system': True},
    {'code': '5060', 'name': 'Travel, Meals & Conveyance Expenses', 'type': 'EXPENSE', 'is_system': True},
    {'code': '5070', 'name': 'Office Supplies, Internet & Utilities', 'type': 'EXPENSE', 'is_system': True},
    {'code': '5080', 'name': 'Bank Charges & Gateway Processing Fees', 'type': 'EXPENSE', 'is_system': True},
    {'code': '5090', 'name': 'Depreciation & Amortization Expense', 'type': 'EXPENSE', 'is_system': True},
]


def ensure_default_chart_of_accounts(company):
    """
    Creates standard Chart of Accounts for a company if not already existing.
    """
    created_accounts = []
    for item in DEFAULT_ACCOUNTS:
        acc, created = Account.objects.get_or_create(
            company=company,
            code=item['code'],
            defaults={
                'name': item['name'],
                'account_type': item['type'],
                'is_system': item['is_system']
            }
        )
        created_accounts.append(acc)
    return created_accounts


def get_account_by_code(company, code):
    ensure_default_chart_of_accounts(company)
    return Account.objects.get(company=company, code=code)


@transaction.atomic
def post_journal_entry(journal_entry, user):
    """
    Validates and posts a journal entry to the general ledger.
    Enforces double-entry debit == credit rule.
    """
    if journal_entry.status != 'DRAFT':
        raise ValueError(f"Journal entry {journal_entry.entry_number} is already {journal_entry.status}.")

    total_debit = journal_entry.total_debit()
    total_credit = journal_entry.total_credit()

    if abs(total_debit - total_credit) > Decimal('0.001'):
        raise ValueError(f"Journal entry {journal_entry.entry_number} is unbalanced. Total Debit (₹{total_debit}) != Total Credit (₹{total_credit}).")

    if journal_entry.lines.count() < 2:
        raise ValueError("A valid journal entry must contain at least 2 lines.")

    journal_entry.status = 'POSTED'
    journal_entry.posted_at = timezone.now()
    journal_entry.posted_by = user
    journal_entry.save()

    AuditEvent.log(
        company=journal_entry.company,
        actor=user,
        action='POST',
        module='accounting',
        model_name='JournalEntry',
        object_id=journal_entry.id,
        object_repr=str(journal_entry),
        changes={'total_debit': str(total_debit), 'total_credit': str(total_credit)}
    )

    return journal_entry


@transaction.atomic
def reverse_journal_entry(journal_entry, user, reason="Correction"):
    """
    Reverses a posted journal entry using standard double-entry accounting reversal.
    Never deletes original posted entries.
    """
    if journal_entry.status != 'POSTED':
        raise ValueError("Only POSTED journal entries can be reversed.")

    rev_number = f"REV-{journal_entry.entry_number}"
    reversal_je = JournalEntry.objects.create(
        company=journal_entry.company,
        entry_number=rev_number,
        date=timezone.now().date(),
        reference=f"Reversal of {journal_entry.entry_number}",
        description=f"Reversal entry for {journal_entry.entry_number}: {reason}",
        source_module=journal_entry.source_module,
        source_id=journal_entry.source_id,
        reversal_of=journal_entry
    )

    # Swap debits and credits
    for line in journal_entry.lines.all():
        JournalLine.objects.create(
            journal=reversal_je,
            account=line.account,
            description=f"Reversal: {line.description}",
            debit=line.credit,
            credit=line.debit,
            party_type=line.party_type,
            party_id=line.party_id
        )

    post_journal_entry(reversal_je, user)
    journal_entry.status = 'REVERSED'
    journal_entry.save(update_fields=['status'])

    return reversal_je


@transaction.atomic
def post_invoice_to_ledger(invoice, user):
    """
    Posts customer tax invoice to general ledger.
    Debit Accounts Receivable (1200)
    Credit Sales Income (4000)
    Credit CGST Payable (2110)
    Credit SGST Payable (2120)
    Credit IGST Payable (2130)
    """
    if invoice.status not in ('DRAFT', 'ISSUED'):
        raise ValueError(f"Invoice {invoice.invoice_number} cannot be posted in state {invoice.status}")

    company = invoice.company
    ar_account = get_account_by_code(company, '1200')
    sales_account = get_account_by_code(company, '4000')

    je = JournalEntry.objects.create(
        company=company,
        entry_number=f"JE-INV-{invoice.invoice_number}",
        date=invoice.date,
        reference=invoice.invoice_number,
        description=f"Invoice #{invoice.invoice_number} for {invoice.customer.name}",
        source_module='INVOICE',
        source_id=str(invoice.id)
    )

    # Debit Accounts Receivable = Grand Total
    JournalLine.objects.create(
        journal=je,
        account=ar_account,
        description=f"AR - {invoice.customer.name}",
        debit=invoice.grand_total,
        credit=Decimal('0.00'),
        party_type='Customer',
        party_id=str(invoice.customer.id)
    )

    # Credit Sales Income = Subtotal
    JournalLine.objects.create(
        journal=je,
        account=sales_account,
        description=f"Sales Revenue - Invoice #{invoice.invoice_number}",
        debit=Decimal('0.00'),
        credit=invoice.subtotal
    )

    # Taxes
    if invoice.cgst_total > Decimal('0.00'):
        cgst_acc = get_account_by_code(company, '2110')
        JournalLine.objects.create(
            journal=je,
            account=cgst_acc,
            description="CGST Tax Collected",
            debit=Decimal('0.00'),
            credit=invoice.cgst_total
        )

    if invoice.sgst_total > Decimal('0.00'):
        sgst_acc = get_account_by_code(company, '2120')
        JournalLine.objects.create(
            journal=je,
            account=sgst_acc,
            description="SGST Tax Collected",
            debit=Decimal('0.00'),
            credit=invoice.sgst_total
        )

    if invoice.igst_total > Decimal('0.00'):
        igst_acc = get_account_by_code(company, '2130')
        JournalLine.objects.create(
            journal=je,
            account=igst_acc,
            description="IGST Tax Collected",
            debit=Decimal('0.00'),
            credit=invoice.igst_total
        )

    post_journal_entry(je, user)
    invoice.status = 'ISSUED'
    invoice.posted_journal = je
    invoice.approved_by = user
    invoice.save()

    return je


@transaction.atomic
def post_payment_to_ledger(payment, user):
    """
    Posts customer payment to general ledger.
    Debit Bank Account (1010)
    Credit Accounts Receivable (1200)
    """
    from invoicing.models import PaymentReceipt

    if payment.status == 'POSTED':
        raise ValueError("Payment is already posted.")

    company = payment.company
    bank_acc = get_account_by_code(company, '1010')
    ar_acc = get_account_by_code(company, '1200')

    je = JournalEntry.objects.create(
        company=company,
        entry_number=f"JE-PAY-{payment.payment_number}",
        date=payment.payment_date,
        reference=payment.reference_number or payment.payment_number,
        description=f"Customer Payment #{payment.payment_number} from {payment.customer.name}",
        source_module='PAYMENT',
        source_id=str(payment.id)
    )

    # Debit Bank Account
    JournalLine.objects.create(
        journal=je,
        account=bank_acc,
        description=f"Received via {payment.get_payment_method_display()}",
        debit=payment.amount,
        credit=Decimal('0.00')
    )

    # Credit Accounts Receivable
    JournalLine.objects.create(
        journal=je,
        account=ar_acc,
        description=f"AR Cleared - {payment.customer.name}",
        debit=Decimal('0.00'),
        credit=payment.amount,
        party_type='Customer',
        party_id=str(payment.customer.id)
    )

    post_journal_entry(je, user)
    payment.status = 'POSTED'
    payment.posted_journal = je
    payment.unallocated_amount = payment.amount
    payment.save()

    # Generate Receipt
    receipt_num = f"RCT-{payment.payment_number.replace('PAY-', '')}"
    receipt, _ = PaymentReceipt.objects.get_or_create(
        company=company,
        payment=payment,
        defaults={
            'receipt_number': receipt_num,
            'customer': payment.customer,
            'receipt_date': payment.payment_date,
            'amount': payment.amount
        }
    )

    return receipt
