from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from accounting.models import (
    Account, JournalEntry, JournalLine, AccountingPeriod,
    TaxRate, BankAccount, Voucher, VoucherLine, BankTransaction,
    FixedAsset, FixedAssetDepreciation
)
from tenants.models import AuditEvent

DEFAULT_ACCOUNTS = [
    # ASSETS
    {'code': '1010', 'name': 'HDFC Main Bank Account', 'type': 'ASSET', 'sub_category': 'BANK', 'cuenta_espanol': 'Cuenta Bancaria Principal', 'is_system': True},
    {'code': '1020', 'name': 'Petty Cash Account', 'type': 'ASSET', 'sub_category': 'CASH', 'cuenta_espanol': 'Caja Chica', 'is_system': True},
    {'code': '1100', 'name': 'Undeposited Funds / Cash Clearing', 'type': 'ASSET', 'sub_category': 'CASH', 'cuenta_espanol': 'Fondos no Depositados', 'is_system': True},
    {'code': '1200', 'name': 'Accounts Receivable (Customers)', 'type': 'ASSET', 'sub_category': 'ACCOUNTS_RECEIVABLE', 'cuenta_espanol': 'Cuentas por Cobrar', 'is_system': True},
    {'code': '1400', 'name': 'Inventory & Hardware Stock', 'type': 'ASSET', 'sub_category': 'INVENTORY', 'cuenta_espanol': 'Inventario de Mercancías', 'is_system': True},
    {'code': '1500', 'name': 'IT & Office Equipment (Fixed Asset)', 'type': 'ASSET', 'sub_category': 'FIXED_ASSET', 'cuenta_espanol': 'Equipo de Cómputo y Oficina', 'is_system': True},
    {'code': '1550', 'name': 'Accumulated Depreciation - Fixed Assets', 'type': 'ASSET', 'sub_category': 'ACCUMULATED_DEPRECIATION', 'cuenta_espanol': 'Depreciación Acumulada', 'is_system': True},
    {'code': '1600', 'name': 'Input Tax Credit (GST Paid)', 'type': 'ASSET', 'sub_category': 'INPUT_TAX', 'cuenta_espanol': 'Impuestos Acreditables', 'is_system': True},
    
    # LIABILITIES
    {'code': '2010', 'name': 'Accounts Payable (Vendors)', 'type': 'LIABILITY', 'sub_category': 'ACCOUNTS_PAYABLE', 'cuenta_espanol': 'Cuentas por Pagar Proveedores', 'is_system': True},
    {'code': '2110', 'name': 'CGST Payable Account', 'type': 'LIABILITY', 'sub_category': 'OUTPUT_TAX', 'cuenta_espanol': 'CGST por Pagar', 'is_system': True},
    {'code': '2120', 'name': 'SGST Payable Account', 'type': 'LIABILITY', 'sub_category': 'OUTPUT_TAX', 'cuenta_espanol': 'SGST por Pagar', 'is_system': True},
    {'code': '2130', 'name': 'IGST Payable Account', 'type': 'LIABILITY', 'sub_category': 'OUTPUT_TAX', 'cuenta_espanol': 'IGST por Pagar', 'is_system': True},
    {'code': '2200', 'name': 'Salaries & Wages Payable', 'type': 'LIABILITY', 'sub_category': 'STATUTORY_DUES', 'cuenta_espanol': 'Sueldos y Salarios por Pagar', 'is_system': True},
    {'code': '2300', 'name': 'TDS Payable / Statutory Dues', 'type': 'LIABILITY', 'sub_category': 'STATUTORY_DUES', 'cuenta_espanol': 'Retenciones de Impuestos', 'is_system': True},
    {'code': '2500', 'name': 'Bank Term Loan / Credit Line', 'type': 'LIABILITY', 'sub_category': 'LONG_TERM_LIABILITY', 'cuenta_espanol': 'Préstamos Bancarios Largo Plazo', 'is_system': False},
    
    # EQUITY
    {'code': '3010', 'name': 'Owner Equity / Share Capital', 'type': 'EQUITY', 'sub_category': 'EQUITY', 'cuenta_espanol': 'Capital Social / Patrimonio', 'is_system': True},
    {'code': '3020', 'name': 'Retained Earnings', 'type': 'EQUITY', 'sub_category': 'RETAINED_EARNINGS', 'cuenta_espanol': 'Utilidades Retenidas', 'is_system': True},
    {'code': '3030', 'name': 'Owner Drawings / Capital Withdrawals', 'type': 'EQUITY', 'sub_category': 'DRAWINGS', 'cuenta_espanol': 'Retiros de Socios', 'is_system': True},
    
    # INCOME
    {'code': '4000', 'name': 'Software Development & Consulting Sales', 'type': 'INCOME', 'sub_category': 'OPERATING_REVENUE', 'cuenta_espanol': 'Ingresos por Servicios de Consultoría', 'is_system': True},
    {'code': '4100', 'name': 'SaaS Subscriptions & License Revenue', 'type': 'INCOME', 'sub_category': 'OPERATING_REVENUE', 'cuenta_espanol': 'Ingresos por Suscripciones SaaS', 'is_system': True},
    {'code': '4200', 'name': 'Other Operating & Interest Income', 'type': 'INCOME', 'sub_category': 'OTHER_INCOME', 'cuenta_espanol': 'Otros Ingresos Operativos', 'is_system': True},
    
    # EXPENSES
    {'code': '5000', 'name': 'Cost of Project Deliverables & Hosting Direct Cost', 'type': 'EXPENSE', 'sub_category': 'COST_OF_SALES', 'cuenta_espanol': 'Costo Directo de Servicios', 'is_system': True},
    {'code': '5010', 'name': 'Employee Salaries & Staff Benefits', 'type': 'EXPENSE', 'sub_category': 'SALARY_EXPENSE', 'cuenta_espanol': 'Nómina y Beneficios de Personal', 'is_system': True},
    {'code': '5020', 'name': 'Office Rent & Workspace Expense', 'type': 'EXPENSE', 'sub_category': 'RENT_EXPENSE', 'cuenta_espanol': 'Renta de Oficinas', 'is_system': True},
    {'code': '5030', 'name': 'Cloud Infrastructure & Software Tools', 'type': 'EXPENSE', 'sub_category': 'OPERATING_EXPENSE', 'cuenta_espanol': 'Infraestructura Cloud y Herramientas', 'is_system': True},
    {'code': '5040', 'name': 'Marketing, Branding & Customer Acquisition', 'type': 'EXPENSE', 'sub_category': 'OPERATING_EXPENSE', 'cuenta_espanol': 'Marketing y Publicidad', 'is_system': True},
    {'code': '5050', 'name': 'Professional, Legal & Audit Fees', 'type': 'EXPENSE', 'sub_category': 'OPERATING_EXPENSE', 'cuenta_espanol': 'Honorarios Profesionales y Legales', 'is_system': True},
    {'code': '5060', 'name': 'Travel, Meals & Conveyance Expenses', 'type': 'EXPENSE', 'sub_category': 'OPERATING_EXPENSE', 'cuenta_espanol': 'Gastos de Viaje y Viáticos', 'is_system': True},
    {'code': '5070', 'name': 'Office Supplies, Internet & Utilities', 'type': 'EXPENSE', 'sub_category': 'OPERATING_EXPENSE', 'cuenta_espanol': 'Servicios Públicos e Internet', 'is_system': True},
    {'code': '5080', 'name': 'Bank Charges & Gateway Processing Fees', 'type': 'EXPENSE', 'sub_category': 'FINANCIAL_EXPENSE', 'cuenta_espanol': 'Comisiones Bancarias', 'is_system': True},
    {'code': '5090', 'name': 'Depreciation & Amortization Expense', 'type': 'EXPENSE', 'sub_category': 'DEPRECIATION_EXPENSE', 'cuenta_espanol': 'Gasto por Depreciación', 'is_system': True},
]

DEFAULT_TAXES = [
    {'name': 'GST 18%', 'rate': Decimal('18.00'), 'type': 'GST', 'gl_code': '2130'},
    {'name': 'CGST 9%', 'rate': Decimal('9.00'), 'type': 'CGST', 'gl_code': '2110'},
    {'name': 'SGST 9%', 'rate': Decimal('9.00'), 'type': 'SGST', 'gl_code': '2120'},
    {'name': 'IGST 18%', 'rate': Decimal('18.00'), 'type': 'IGST', 'gl_code': '2130'},
    {'name': 'TDS 10%', 'rate': Decimal('10.00'), 'type': 'TDS', 'gl_code': '2300'},
]


def ensure_default_chart_of_accounts(company):
    """
    Creates standard Chart of Accounts for a company if not already existing.
    Updates existing records with rich sub_categories and Spanish aliases.
    Also ensures default bank accounts and standard tax rates.
    """
    created_accounts = []
    for item in DEFAULT_ACCOUNTS:
        acc, created = Account.objects.get_or_create(
            company=company,
            code=item['code'],
            defaults={
                'name': item['name'],
                'account_type': item['type'],
                'sub_category': item.get('sub_category', 'OPERATING_EXPENSE'),
                'cuenta_espanol': item.get('cuenta_espanol', ''),
                'is_system': item['is_system']
            }
        )
        if not created:
            # Backfill any missing fields
            updated = False
            if not acc.sub_category or acc.sub_category == 'OPERATING_EXPENSE':
                if item.get('sub_category') and acc.sub_category != item['sub_category']:
                    acc.sub_category = item['sub_category']
                    updated = True
            if not acc.cuenta_espanol and item.get('cuenta_espanol'):
                acc.cuenta_espanol = item['cuenta_espanol']
                updated = True
            if updated:
                acc.save()
        created_accounts.append(acc)

    # Ensure default bank accounts
    hdfc_acc = Account.objects.filter(company=company, code='1010').first()
    petty_acc = Account.objects.filter(company=company, code='1020').first()
    
    if hdfc_acc:
        BankAccount.objects.get_or_create(
            company=company,
            account_number='50200084920192',
            defaults={
                'bank_name': 'HDFC Bank',
                'account_name': 'HDFC Main Operating Account',
                'account_type': 'BANK',
                'ifsc_code': 'HDFC0001234',
                'branch_name': 'MG Road Branch, Bengaluru',
                'gl_account': hdfc_acc,
                'opening_balance': hdfc_acc.opening_balance,
                'current_balance': hdfc_acc.current_balance(),
                'status': 'ACTIVE',
                'description': 'Primary business operating bank account for collections & disbursements'
            }
        )

    if petty_acc:
        BankAccount.objects.get_or_create(
            company=company,
            account_number='CASH-001',
            defaults={
                'bank_name': 'Cash Drawer',
                'account_name': 'Head Office Petty Cash',
                'account_type': 'CASH',
                'gl_account': petty_acc,
                'opening_balance': petty_acc.opening_balance,
                'current_balance': petty_acc.current_balance(),
                'status': 'ACTIVE',
                'description': 'Petty cash for office day-to-day administrative expenses'
            }
        )

    # Ensure default tax rates
    for tax in DEFAULT_TAXES:
        gl = Account.objects.filter(company=company, code=tax['gl_code']).first()
        TaxRate.objects.get_or_create(
            company=company,
            name=tax['name'],
            defaults={
                'rate': tax['rate'],
                'tax_type': tax['type'],
                'gl_account': gl,
                'is_active': True
            }
        )

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
def create_and_post_voucher(company, voucher_type, date, narration, lines_data, user,
                            reference_number='', party_type='OTHER', party_id='', party_name='',
                            auto_set_off=True):
    """
    Full double-entry voucher creation engine matching Ola Cars.
    Generates sequence numbers (RV-, PMT-, CN-, SV-, PV-, JV-),
    creates Voucher & VoucherLine records, creates and posts balanced JournalEntry in GL,
    and runs Auto Set-off for Customer Invoices or Bank Contra Transfers.
    """
    ensure_default_chart_of_accounts(company)

    # 1. Generate sequential Voucher Number
    prefix_map = {
        'SALES': 'SV',
        'PURCHASE': 'PV',
        'RECEIPT': 'RV',
        'PAYMENT': 'PMT',
        'JOURNAL': 'JV',
        'CONTRA': 'CN',
    }
    prefix = prefix_map.get(voucher_type, 'VCH')
    date_str = date.strftime('%Y%m%d') if hasattr(date, 'strftime') else timezone.now().strftime('%Y%m%d')
    
    count = Voucher.objects.filter(company=company, voucher_type=voucher_type).count() + 1
    voucher_number = f"{prefix}-{date_str}-{count:04d}"
    # Ensure uniqueness
    attempts = 0
    while Voucher.objects.filter(company=company, voucher_number=voucher_number).exists() and attempts < 100:
        count += 1
        voucher_number = f"{prefix}-{date_str}-{count:04d}"
        attempts += 1

    # 2. Validate Lines
    if not lines_data or len(lines_data) < 2:
        raise ValueError("A voucher must contain at least 2 transaction lines.")

    total_debit = Decimal('0.00')
    total_credit = Decimal('0.00')

    for l in lines_data:
        amt = Decimal(str(l.get('amount', '0.00')))
        if amt <= Decimal('0.00'):
            raise ValueError("All line amounts must be positive numbers.")
        etype = l.get('entry_type', '').upper()
        if etype == 'DEBIT':
            total_debit += amt
        elif etype == 'CREDIT':
            total_credit += amt
        else:
            raise ValueError(f"Invalid entry type: {etype}. Must be DEBIT or CREDIT.")

    if abs(total_debit - total_credit) > Decimal('0.001'):
        raise ValueError(f"Total Debits (₹{total_debit}) must equal Total Credits (₹{total_credit}).")

    voucher_total = total_debit

    # 3. Create Voucher Header
    voucher = Voucher.objects.create(
        company=company,
        voucher_number=voucher_number,
        date=date,
        voucher_type=voucher_type,
        narration=narration,
        reference_number=reference_number,
        party_type=party_type,
        party_id=party_id,
        party_name=party_name,
        total_amount=voucher_total,
        status='POSTED',
        auto_set_off=auto_set_off,
        created_by=user,
        posted_by=user,
        posted_at=timezone.now()
    )

    # 4. Create Voucher Lines
    for l in lines_data:
        acc_id = l.get('account_id')
        acc = Account.objects.get(id=acc_id, company=company)
        amt = Decimal(str(l.get('amount', '0.00')))
        desc = l.get('description', '') or narration
        tax_id = l.get('tax_id')
        tax = TaxRate.objects.filter(id=tax_id, company=company).first() if tax_id else None
        tax_amt = Decimal(str(l.get('tax_amount', '0.00')))

        VoucherLine.objects.create(
            voucher=voucher,
            account=acc,
            entry_type=l['entry_type'].upper(),
            amount=amt,
            description=desc,
            tax_rate=tax,
            tax_amount=tax_amt
        )

    # 5. Create GL Journal Entry linked to Voucher
    je = JournalEntry.objects.create(
        company=company,
        entry_number=f"JE-{voucher_number}",
        date=date,
        reference=reference_number or voucher_number,
        description=f"[{voucher.get_voucher_type_display()}] {narration}",
        source_module='VOUCHER',
        source_id=str(voucher.id),
        status='POSTED',
        posted_at=timezone.now(),
        posted_by=user
    )

    for l in lines_data:
        acc = Account.objects.get(id=l['account_id'], company=company)
        amt = Decimal(str(l['amount']))
        etype = l['entry_type'].upper()
        JournalLine.objects.create(
            journal=je,
            account=acc,
            description=l.get('description', '') or narration,
            debit=amt if etype == 'DEBIT' else Decimal('0.00'),
            credit=amt if etype == 'CREDIT' else Decimal('0.00'),
            party_type=party_type,
            party_id=party_id
        )

    voucher.journal_entry = je
    voucher.save(update_fields=['journal_entry'])

    # 6. Auto Set-Off: Customer Invoices on RECEIPT Voucher
    if voucher_type == 'RECEIPT' and auto_set_off and party_type == 'CUSTOMER' and party_id:
        try:
            from invoicing.models import Invoice, CustomerPayment, PaymentAllocation
            from crm.models import Customer

            customer = Customer.objects.filter(id=party_id, company=company).first()
            if customer:
                # Create a payment record
                pay_num = f"PAY-{voucher_number.replace('RV-', '')}"
                cust_payment, _ = CustomerPayment.objects.get_or_create(
                    company=company,
                    payment_number=pay_num,
                    defaults={
                        'customer': customer,
                        'payment_date': date,
                        'amount': voucher_total,
                        'unallocated_amount': voucher_total,
                        'payment_method': 'BANK_TRANSFER',
                        'reference_number': reference_number or voucher_number,
                        'status': 'POSTED',
                        'posted_journal': je,
                        'notes': f"Auto-created from Voucher {voucher_number}"
                    }
                )

                # Allocate FIFO across open invoices
                open_invoices = Invoice.objects.filter(
                    company=company,
                    customer=customer,
                    status__in=['ISSUED', 'PARTIALLY_PAID']
                ).order_by('date')

                remaining_to_allocate = voucher_total

                for inv in open_invoices:
                    if remaining_to_allocate <= Decimal('0.00'):
                        break
                    due = inv.balance_due
                    alloc = min(due, remaining_to_allocate)
                    if alloc > Decimal('0.00'):
                        inv.paid_amount += alloc
                        inv.balance_due -= alloc
                        if inv.balance_due <= Decimal('0.01'):
                            inv.balance_due = Decimal('0.00')
                            inv.status = 'PAID'
                        else:
                            inv.status = 'PARTIALLY_PAID'
                        inv.save()

                        PaymentAllocation.objects.create(
                            payment=cust_payment,
                            invoice=inv,
                            allocated_amount=alloc
                        )
                        remaining_to_allocate -= alloc

                cust_payment.unallocated_amount = remaining_to_allocate
                cust_payment.save()
        except Exception as e:
            print(f"[create_and_post_voucher] Invoice auto-set-off warning: {e}")

    # 7. CONTRA Voucher: Transfer between bank/cash accounts
    if voucher_type == 'CONTRA':
        try:
            debit_line = next((l for l in lines_data if l['entry_type'].upper() == 'DEBIT'), None)
            credit_line = next((l for l in lines_data if l['entry_type'].upper() == 'CREDIT'), None)
            
            if debit_line and credit_line:
                dest_acc = Account.objects.get(id=debit_line['account_id'], company=company)
                source_acc = Account.objects.get(id=credit_line['account_id'], company=company)

                dest_bank = BankAccount.objects.filter(gl_account=dest_acc, company=company).first()
                source_bank = BankAccount.objects.filter(gl_account=source_acc, company=company).first()

                amt = Decimal(str(debit_line['amount']))

                if source_bank:
                    source_bank.current_balance -= amt
                    source_bank.save()
                    BankTransaction.objects.create(
                        bank_account=source_bank,
                        date=date,
                        transaction_type='WITHDRAWAL',
                        amount=amt,
                        reference=reference_number or voucher_number,
                        description=f"Contra Transfer to {dest_acc.name}: {narration}",
                        party_name=dest_acc.name,
                        voucher=voucher,
                        status='CLEARED',
                        running_balance=source_bank.current_balance
                    )

                if dest_bank:
                    dest_bank.current_balance += amt
                    dest_bank.save()
                    BankTransaction.objects.create(
                        bank_account=dest_bank,
                        date=date,
                        transaction_type='DEPOSIT',
                        amount=amt,
                        reference=reference_number or voucher_number,
                        description=f"Contra Transfer from {source_acc.name}: {narration}",
                        party_name=source_acc.name,
                        voucher=voucher,
                        status='CLEARED',
                        running_balance=dest_bank.current_balance
                    )
        except Exception as e:
            print(f"[create_and_post_voucher] Contra bank sync warning: {e}")

    return voucher


@transaction.atomic
def post_asset_depreciation(asset, user):
    """
    Calculates and posts monthly depreciation for a FixedAsset into the GL.
    Debit: Depreciation Expense Account (5090)
    Credit: Accumulated Depreciation Account (1550)
    """
    company = asset.company
    ensure_default_chart_of_accounts(company)

    monthly_depr = asset.monthly_depreciation_amount()
    if monthly_depr <= Decimal('0.00'):
        raise ValueError(f"Asset {asset.asset_code} has no remaining depreciable value.")

    accum_account = asset.accumulated_depreciation_account or get_account_by_code(company, '1550')
    expense_account = asset.depreciation_account or get_account_by_code(company, '5090')

    # Determine period index
    last_entry = asset.depreciation_entries.order_by('-period_index').first()
    next_index = (last_entry.period_index + 1) if last_entry else 1
    today = timezone.now().date()

    # Create Journal Entry
    je_num = f"JE-DEP-{asset.asset_code}-P{next_index}"
    je = JournalEntry.objects.create(
        company=company,
        entry_number=je_num,
        date=today,
        reference=asset.asset_code,
        description=f"Depreciation Period #{next_index} for Asset {asset.asset_code} - {asset.name}",
        source_module='DEPRECIATION',
        source_id=str(asset.id),
        status='POSTED',
        posted_at=timezone.now(),
        posted_by=user
    )

    # Debit Depreciation Expense
    JournalLine.objects.create(
        journal=je,
        account=expense_account,
        description=f"Depreciation Expense - {asset.name}",
        debit=monthly_depr,
        credit=Decimal('0.00')
    )

    # Credit Accumulated Depreciation
    JournalLine.objects.create(
        journal=je,
        account=accum_account,
        description=f"Accum. Depreciation - {asset.name}",
        debit=Decimal('0.00'),
        credit=monthly_depr
    )

    # Update Asset
    asset.accumulated_depreciation += monthly_depr
    if asset.accumulated_depreciation >= (asset.purchase_cost - asset.salvage_value):
        asset.accumulated_depreciation = asset.purchase_cost - asset.salvage_value
    asset.save(update_fields=['accumulated_depreciation'])

    # Create Schedule record
    sched = FixedAssetDepreciation.objects.create(
        asset=asset,
        period_index=next_index,
        period_date=today,
        depreciation_amount=monthly_depr,
        accumulated_depreciation=asset.accumulated_depreciation,
        book_value=asset.book_value(),
        status='POSTED',
        journal_entry=je,
        posted_at=timezone.now()
    )

    return sched


def generate_financial_statements(company, start_date=None, end_date=None):
    """
    Comprehensive financial reporting engine matching Ola Cars ReportingService.
    Calculates:
    - Trial Balance (Debits, Credits, Net)
    - Profit & Loss Statement (Operating Revenue, Cost of Sales, Gross Profit, Expenses, EBITDA, Net Profit/Loss)
    - Balance Sheet (Current Assets, Fixed Assets, Liabilities, Equity, Balanced Check)
    - Banking Overview (Cash & Bank positions)
    """
    ensure_default_chart_of_accounts(company)
    accounts = Account.objects.filter(company=company, is_active=True).order_by('code')

    trial_balance = []
    total_tb_debit = Decimal('0.00')
    total_tb_credit = Decimal('0.00')

    # Category buckets
    operating_revenue = Decimal('0.00')
    other_income = Decimal('0.00')
    cost_of_sales = Decimal('0.00')
    operating_expenses = Decimal('0.00')
    salary_expenses = Decimal('0.00')
    rent_expenses = Decimal('0.00')
    depreciation_expenses = Decimal('0.00')
    financial_expenses = Decimal('0.00')
    other_expenses = Decimal('0.00')

    current_assets = Decimal('0.00')
    cash_and_bank = Decimal('0.00')
    accounts_receivable = Decimal('0.00')
    inventory = Decimal('0.00')
    fixed_assets_gross = Decimal('0.00')
    accumulated_depreciation = Decimal('0.00')
    other_assets = Decimal('0.00')

    current_liabilities = Decimal('0.00')
    accounts_payable = Decimal('0.00')
    taxes_payable = Decimal('0.00')
    statutory_dues = Decimal('0.00')
    long_term_liabilities = Decimal('0.00')

    owner_equity = Decimal('0.00')
    retained_earnings = Decimal('0.00')
    drawings = Decimal('0.00')

    income_accounts_list = []
    expense_accounts_list = []
    asset_accounts_list = []
    liability_accounts_list = []
    equity_accounts_list = []

    for acc in accounts:
        dr = acc.get_debit_total(start_date=start_date, end_date=end_date)
        cr = acc.get_credit_total(start_date=start_date, end_date=end_date)
        bal = acc.current_balance(as_of_date=end_date)
        
        # Trial balance uses normal balances
        if acc.account_type in ('ASSET', 'EXPENSE'):
            net_dr = max(Decimal('0.00'), bal)
            net_cr = abs(min(Decimal('0.00'), bal))
        else:
            net_cr = max(Decimal('0.00'), bal)
            net_dr = abs(min(Decimal('0.00'), bal))

        total_tb_debit += net_dr
        total_tb_credit += net_cr

        item_data = {
            'account': acc,
            'debit': net_dr,
            'credit': net_cr,
            'balance': bal,
            'period_dr': dr,
            'period_cr': cr
        }
        trial_balance.append(item_data)

        # Classification
        sub = acc.sub_category or ''
        atype = acc.account_type

        if atype == 'INCOME':
            income_accounts_list.append(item_data)
            if sub == 'OPERATING_REVENUE':
                operating_revenue += bal
            else:
                other_income += bal

        elif atype == 'EXPENSE':
            expense_accounts_list.append(item_data)
            if sub == 'COST_OF_SALES':
                cost_of_sales += bal
            elif sub == 'SALARY_EXPENSE':
                salary_expenses += bal
                operating_expenses += bal
            elif sub == 'RENT_EXPENSE':
                rent_expenses += bal
                operating_expenses += bal
            elif sub == 'DEPRECIATION_EXPENSE':
                depreciation_expenses += bal
            elif sub == 'FINANCIAL_EXPENSE':
                financial_expenses += bal
                operating_expenses += bal
            else:
                other_expenses += bal
                operating_expenses += bal

        elif atype == 'ASSET':
            asset_accounts_list.append(item_data)
            if sub in ('BANK', 'CASH'):
                cash_and_bank += bal
                current_assets += bal
            elif sub == 'ACCOUNTS_RECEIVABLE':
                accounts_receivable += bal
                current_assets += bal
            elif sub == 'INVENTORY':
                inventory += bal
                current_assets += bal
            elif sub == 'FIXED_ASSET':
                fixed_assets_gross += bal
            elif sub == 'ACCUMULATED_DEPRECIATION':
                # accumulated depreciation is a contra-asset (credit balance)
                accumulated_depreciation += abs(bal)
            else:
                other_assets += bal
                current_assets += bal

        elif atype == 'LIABILITY':
            liability_accounts_list.append(item_data)
            if sub == 'ACCOUNTS_PAYABLE':
                accounts_payable += bal
                current_liabilities += bal
            elif sub == 'OUTPUT_TAX':
                taxes_payable += bal
                current_liabilities += bal
            elif sub == 'STATUTORY_DUES':
                statutory_dues += bal
                current_liabilities += bal
            elif sub == 'LONG_TERM_LIABILITY':
                long_term_liabilities += bal
            else:
                current_liabilities += bal

        elif atype == 'EQUITY':
            equity_accounts_list.append(item_data)
            if sub == 'EQUITY':
                owner_equity += bal
            elif sub == 'RETAINED_EARNINGS':
                retained_earnings += bal
            elif sub == 'DRAWINGS':
                drawings += bal

    # Profit & Loss calculations
    total_revenue = operating_revenue + other_income
    gross_profit = total_revenue - cost_of_sales
    gross_margin_pct = round((gross_profit / total_revenue * Decimal('100.00')), 1) if total_revenue > Decimal('0.00') else Decimal('0.00')
    
    total_operating_expenses = operating_expenses
    ebitda = gross_profit - total_operating_expenses
    net_profit = ebitda - depreciation_expenses
    net_margin_pct = round((net_profit / total_revenue * Decimal('100.00')), 1) if total_revenue > Decimal('0.00') else Decimal('0.00')

    # Balance Sheet calculations
    net_fixed_assets = fixed_assets_gross - accumulated_depreciation
    total_assets = current_assets + net_fixed_assets + other_assets
    total_liabilities = current_liabilities + long_term_liabilities
    total_equity = owner_equity + retained_earnings - drawings + net_profit
    total_liabilities_and_equity = total_liabilities + total_equity
    is_balanced = abs(total_assets - total_liabilities_and_equity) < Decimal('1.00')

    # Banking summary
    bank_accounts = BankAccount.objects.filter(company=company, status='ACTIVE')

    return {
        'trial_balance': trial_balance,
        'total_tb_debit': total_tb_debit,
        'total_tb_credit': total_tb_credit,
        'tb_is_balanced': abs(total_tb_debit - total_tb_credit) < Decimal('0.01'),

        # P&L metrics
        'operating_revenue': operating_revenue,
        'other_income': other_income,
        'total_revenue': total_revenue,
        'cost_of_sales': cost_of_sales,
        'gross_profit': gross_profit,
        'gross_margin_pct': gross_margin_pct,
        'operating_expenses': total_operating_expenses,
        'salary_expenses': salary_expenses,
        'rent_expenses': rent_expenses,
        'financial_expenses': financial_expenses,
        'other_expenses': other_expenses,
        'ebitda': ebitda,
        'depreciation_expenses': depreciation_expenses,
        'net_profit': net_profit,
        'net_margin_pct': net_margin_pct,
        'income_accounts': income_accounts_list,
        'expense_accounts': expense_accounts_list,

        # Balance Sheet metrics
        'cash_and_bank': cash_and_bank,
        'accounts_receivable': accounts_receivable,
        'inventory': inventory,
        'current_assets': current_assets,
        'fixed_assets_gross': fixed_assets_gross,
        'accumulated_depreciation': accumulated_depreciation,
        'net_fixed_assets': net_fixed_assets,
        'total_assets': total_assets,

        'accounts_payable': accounts_payable,
        'taxes_payable': taxes_payable,
        'statutory_dues': statutory_dues,
        'current_liabilities': current_liabilities,
        'long_term_liabilities': long_term_liabilities,
        'total_liabilities': total_liabilities,

        'owner_equity': owner_equity,
        'retained_earnings': retained_earnings,
        'drawings': drawings,
        'current_period_profit': net_profit,
        'total_equity': total_equity,

        'total_liabilities_and_equity': total_liabilities_and_equity,
        'bs_is_balanced': is_balanced,
        'asset_accounts': asset_accounts_list,
        'liability_accounts': liability_accounts_list,
        'equity_accounts': equity_accounts_list,

        # Banking
        'bank_accounts': bank_accounts,
    }


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
