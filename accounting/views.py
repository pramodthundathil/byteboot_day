import csv
from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse
from django.utils import timezone
from django.db import models
from django.db.models import Q

from accounting.models import (
    Account, JournalEntry, JournalLine, AccountingPeriod,
    TaxRate, BankAccount, Voucher, VoucherLine, BankTransaction,
    FixedAsset, FixedAssetDepreciation
)
from accounting.services import (
    ensure_default_chart_of_accounts,
    get_account_by_code,
    post_journal_entry,
    reverse_journal_entry,
    create_and_post_voucher,
    post_asset_depreciation,
    generate_financial_statements
)
from tenants.decorators import require_permission
from crm.models import Customer


# ==============================================================================
# 1. CHART OF ACCOUNTS & ACCOUNT LEDGER DRILL-DOWN
# ==============================================================================

@login_required
@require_permission('accounting', 'view')
def chart_of_accounts(request):
    company = request.company
    ensure_default_chart_of_accounts(company)

    type_filter = request.GET.get('type', '').strip().upper()
    sub_filter = request.GET.get('sub', '').strip()
    query = request.GET.get('q', '').strip()

    accounts = Account.objects.filter(company=company)
    if type_filter and type_filter != 'ALL':
        accounts = accounts.filter(account_type=type_filter)
    if sub_filter:
        accounts = accounts.filter(sub_category=sub_filter)
    if query:
        accounts = accounts.filter(
            Q(name__icontains=query) |
            Q(code__icontains=query) |
            Q(cuenta_espanol__icontains=query) |
            Q(description__icontains=query)
        )

    account_list = []
    category_totals = {
        'ASSET': Decimal('0.00'),
        'LIABILITY': Decimal('0.00'),
        'EQUITY': Decimal('0.00'),
        'INCOME': Decimal('0.00'),
        'EXPENSE': Decimal('0.00')
    }

    # All company accounts for parent selector and category totals
    all_accounts = Account.objects.filter(company=company, is_active=True).order_by('code')
    for acc in all_accounts:
        bal = acc.current_balance()
        if acc.account_type in category_totals:
            category_totals[acc.account_type] += bal

    for acc in accounts:
        bal = acc.current_balance()
        account_list.append({
            'account': acc,
            'balance': bal
        })

    return render(request, 'accounting/chart_of_accounts.html', {
        'accounts_with_balance': account_list,
        'all_accounts': all_accounts,
        'category_totals': category_totals,
        'type_filter': type_filter or 'ALL',
        'sub_filter': sub_filter,
        'query': query,
        'sub_category_choices': Account.SUB_CATEGORY_CHOICES,
        'type_choices': Account.TYPE_CHOICES,
        'active_tab': 'accounting',
        'active_sub': 'accounts'
    })


@login_required
@require_permission('accounting', 'create')
def account_create(request):
    company = request.company
    if request.method == 'POST':
        code = request.POST.get('code', '').strip()
        name = request.POST.get('name', '').strip()
        account_type = request.POST.get('account_type', 'EXPENSE')
        sub_category = request.POST.get('sub_category', '')
        parent_id = request.POST.get('parent_id')
        currency = request.POST.get('currency', 'INR').strip()
        account_number = request.POST.get('account_number', '').strip()
        cuenta_espanol = request.POST.get('cuenta_espanol', '').strip()
        opening_balance = Decimal(request.POST.get('opening_balance', '0.00') or '0.00')
        description = request.POST.get('description', '').strip()

        if not code or not name:
            messages.error(request, "Account Code and Name are required.")
            return redirect('accounting:chart_of_accounts')

        if Account.objects.filter(company=company, code=code).exists():
            messages.error(request, f"Account Code '{code}' already exists.")
            return redirect('accounting:chart_of_accounts')

        parent_acc = Account.objects.filter(company=company, id=parent_id).first() if parent_id else None

        acc = Account.objects.create(
            company=company,
            code=code,
            name=name,
            account_type=account_type,
            sub_category=sub_category or 'OPERATING_EXPENSE',
            parent=parent_acc,
            currency=currency,
            account_number=account_number,
            cuenta_espanol=cuenta_espanol,
            opening_balance=opening_balance,
            description=description,
            is_system=False
        )
        messages.success(request, f"Account #{acc.code} ({acc.name}) created successfully.")

    return redirect('accounting:chart_of_accounts')


@login_required
@require_permission('accounting', 'edit')
def account_edit(request, account_id):
    company = request.company
    account = get_object_or_404(Account, id=account_id, company=company)

    if request.method == 'POST':
        account.name = request.POST.get('name', '').strip() or account.name
        account.sub_category = request.POST.get('sub_category', account.sub_category)
        account.cuenta_espanol = request.POST.get('cuenta_espanol', '').strip()
        account.account_number = request.POST.get('account_number', '').strip()
        account.description = request.POST.get('description', '').strip()
        parent_id = request.POST.get('parent_id')
        if parent_id:
            account.parent = Account.objects.filter(company=company, id=parent_id).first()
        else:
            account.parent = None
        account.save()
        messages.success(request, f"Account #{account.code} updated successfully.")

    return redirect('accounting:chart_of_accounts')


@login_required
@require_permission('accounting', 'view')
def account_detail(request, account_id):
    """
    Detailed Account Ledger Drill-Down view.
    Displays running balance and all posted journal lines for this specific account.
    """
    company = request.company
    account = get_object_or_404(Account, id=account_id, company=company)

    start_date = request.GET.get('start_date', '')
    end_date = request.GET.get('end_date', '')

    lines = JournalLine.objects.filter(
        journal__company=company,
        journal__status='POSTED',
        account=account
    ).select_related('journal').order_by('journal__date', 'journal__id', 'id')

    # Calculate Opening Balance before start_date if specified
    opening_balance = account.opening_balance
    if start_date:
        prior_lines = JournalLine.objects.filter(
            journal__company=company,
            journal__status='POSTED',
            account=account,
            journal__date__lt=start_date
        )
        prior_debit = sum((l.debit for l in prior_lines), Decimal('0.00'))
        prior_credit = sum((l.credit for l in prior_lines), Decimal('0.00'))
        if account.account_type in ('ASSET', 'EXPENSE'):
            opening_balance = opening_balance + prior_debit - prior_credit
        else:
            opening_balance = opening_balance + prior_credit - prior_debit

        lines = lines.filter(journal__date__gte=start_date)

    if end_date:
        lines = lines.filter(journal__date__lte=end_date)

    # Compute running balances
    running = opening_balance
    entries = []
    total_period_debit = Decimal('0.00')
    total_period_credit = Decimal('0.00')

    for l in lines:
        total_period_debit += l.debit
        total_period_credit += l.credit
        if account.account_type in ('ASSET', 'EXPENSE'):
            running = running + l.debit - l.credit
        else:
            running = running + l.credit - l.debit

        entries.append({
            'line': l,
            'journal': l.journal,
            'debit': l.debit,
            'credit': l.credit,
            'running_balance': running
        })

    return render(request, 'accounting/account_detail.html', {
        'account': account,
        'entries': entries,
        'opening_balance': opening_balance,
        'closing_balance': running,
        'total_period_debit': total_period_debit,
        'total_period_credit': total_period_credit,
        'start_date': start_date,
        'end_date': end_date,
        'active_tab': 'accounting',
        'active_sub': 'accounts'
    })


@login_required
@require_permission('accounting', 'view')
def account_export_csv(request):
    company = request.company
    accounts = Account.objects.filter(company=company).order_by('code')

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="Chart_of_Accounts_{company.code}.csv"'

    writer = csv.writer(response)
    writer.writerow(['Code', 'Name', 'Category', 'Sub-Category', 'Currency', 'Spanish Name', 'Opening Balance', 'Current Balance', 'Description'])

    for acc in accounts:
        writer.writerow([
            acc.code,
            acc.name,
            acc.get_account_type_display(),
            acc.get_sub_category_display(),
            acc.currency,
            acc.cuenta_espanol,
            acc.opening_balance,
            acc.current_balance(),
            acc.description
        ])

    return response


@login_required
@require_permission('accounting', 'create')
def account_bulk_upload(request):
    company = request.company
    if request.method == 'POST' and request.FILES.get('csv_file'):
        csv_file = request.FILES['csv_file']
        decoded = csv_file.read().decode('utf-8').splitlines()
        reader = csv.DictReader(decoded)
        imported_count = 0
        for row in reader:
            code = (row.get('Code') or row.get('code') or '').strip()
            name = (row.get('Name') or row.get('name') or '').strip()
            atype = (row.get('Category') or row.get('category') or row.get('type') or 'EXPENSE').strip().upper()
            sub = (row.get('Sub-Category') or row.get('sub_category') or '').strip().upper()
            cuenta = (row.get('Spanish Name') or row.get('cuenta_espanol') or '').strip()
            desc = (row.get('Description') or row.get('description') or '').strip()

            if code and name:
                Account.objects.update_or_create(
                    company=company,
                    code=code,
                    defaults={
                        'name': name,
                        'account_type': atype if atype in ['ASSET', 'LIABILITY', 'EQUITY', 'INCOME', 'EXPENSE'] else 'EXPENSE',
                        'sub_category': sub or 'OPERATING_EXPENSE',
                        'cuenta_espanol': cuenta,
                        'description': desc
                    }
                )
                imported_count += 1
        messages.success(request, f"Successfully processed {imported_count} accounts from CSV.")
    return redirect('accounting:chart_of_accounts')


# ==============================================================================
# 2. GENERAL LEDGER
# ==============================================================================

@login_required
@require_permission('accounting', 'view')
def general_ledger(request):
    company = request.company
    ensure_default_chart_of_accounts(company)

    account_id = request.GET.get('account')
    source_filter = request.GET.get('source', '')
    start_date = request.GET.get('start_date', '')
    end_date = request.GET.get('end_date', '')
    query = request.GET.get('q', '').strip()

    accounts = Account.objects.filter(company=company, is_active=True).order_by('code')

    lines = JournalLine.objects.filter(
        journal__company=company,
        journal__status='POSTED'
    ).select_related('journal', 'account').order_by('journal__date', 'journal__id', 'id')

    selected_account = None
    if account_id:
        selected_account = Account.objects.filter(company=company, id=account_id).first()
        if selected_account:
            lines = lines.filter(account=selected_account)

    if source_filter:
        lines = lines.filter(journal__source_module=source_filter)
    if start_date:
        lines = lines.filter(journal__date__gte=start_date)
    if end_date:
        lines = lines.filter(journal__date__lte=end_date)
    if query:
        lines = lines.filter(
            Q(journal__entry_number__icontains=query) |
            Q(journal__reference__icontains=query) |
            Q(description__icontains=query) |
            Q(party_id__icontains=query) |
            Q(account__name__icontains=query) |
            Q(account__code__icontains=query)
        )

    # Compute running balance
    total_debit = Decimal('0.00')
    total_credit = Decimal('0.00')
    running = Decimal('0.00')
    ledger_entries = []

    for l in lines[:1000]:  # Cap at 1000 for responsive rendering
        total_debit += l.debit
        total_credit += l.credit
        running += (l.debit - l.credit)
        ledger_entries.append({
            'line': l,
            'journal': l.journal,
            'account': l.account,
            'debit': l.debit,
            'credit': l.credit,
            'running_balance': running
        })

    return render(request, 'accounting/general_ledger.html', {
        'ledger_entries': ledger_entries,
        'accounts': accounts,
        'selected_account': selected_account,
        'total_debit': total_debit,
        'total_credit': total_credit,
        'source_filter': source_filter,
        'start_date': start_date,
        'end_date': end_date,
        'query': query,
        'source_choices': JournalEntry.SOURCE_MODULE_CHOICES,
        'active_tab': 'accounting',
        'active_sub': 'ledger'
    })


# ==============================================================================
# 3. VOUCHER SYSTEM (Sales, Purchase, Receipt, Payment, Journal, Contra)
# ==============================================================================

@login_required
@require_permission('accounting', 'view')
def voucher_list(request):
    company = request.company
    ensure_default_chart_of_accounts(company)

    voucher_type = request.GET.get('type', '').strip().upper()
    status_filter = request.GET.get('status', '').strip().upper()
    query = request.GET.get('q', '').strip()

    vouchers = Voucher.objects.filter(company=company)
    if voucher_type:
        vouchers = vouchers.filter(voucher_type=voucher_type)
    if status_filter:
        vouchers = vouchers.filter(status=status_filter)
    if query:
        vouchers = vouchers.filter(
            Q(voucher_number__icontains=query) |
            Q(reference_number__icontains=query) |
            Q(narration__icontains=query) |
            Q(party_name__icontains=query)
        )

    # Summary metrics
    all_vouchers = Voucher.objects.filter(company=company, status='POSTED')
    total_receipts = sum((v.total_amount for v in all_vouchers.filter(voucher_type='RECEIPT')), Decimal('0.00'))
    total_payments = sum((v.total_amount for v in all_vouchers.filter(voucher_type='PAYMENT')), Decimal('0.00'))
    total_contras = sum((v.total_amount for v in all_vouchers.filter(voucher_type='CONTRA')), Decimal('0.00'))
    total_count = Voucher.objects.filter(company=company).count()

    return render(request, 'accounting/voucher_list.html', {
        'vouchers': vouchers,
        'voucher_type': voucher_type,
        'status_filter': status_filter,
        'query': query,
        'total_receipts': total_receipts,
        'total_payments': total_payments,
        'total_contras': total_contras,
        'total_count': total_count,
        'voucher_types': Voucher.VOUCHER_TYPE_CHOICES,
        'active_tab': 'accounting',
        'active_sub': 'vouchers'
    })


@login_required
@require_permission('accounting', 'create')
def voucher_create(request):
    company = request.company
    ensure_default_chart_of_accounts(company)

    accounts = Account.objects.filter(company=company, is_active=True).order_by('code')
    customers = Customer.objects.filter(company=company)
    bank_accounts = BankAccount.objects.filter(company=company, status='ACTIVE')

    if request.method == 'POST':
        v_type = request.POST.get('voucher_type', 'RECEIPT')
        date_val = request.POST.get('date', timezone.now().date())
        narration = request.POST.get('narration', '').strip()
        ref_num = request.POST.get('reference_number', '').strip()
        party_type = request.POST.get('party_type', 'OTHER')
        party_id = request.POST.get('party_id', '')
        party_name = request.POST.get('party_name', '').strip()
        auto_set_off = request.POST.get('auto_set_off') == 'on'

        # If party_type is CUSTOMER and customer id provided, get name
        if party_type == 'CUSTOMER' and party_id:
            cust = Customer.objects.filter(id=party_id, company=company).first()
            if cust:
                party_name = cust.name

        # Parse lines
        lines_data = []
        # Dynamic lines submitted via lines_count or line arrays
        line_indices = request.POST.getlist('line_index')
        if not line_indices:
            # Fallback for simple 2-line form
            acc_debit_id = request.POST.get('debit_account')
            acc_credit_id = request.POST.get('credit_account')
            amount = Decimal(request.POST.get('amount', '0.00') or '0.00')

            if acc_debit_id and acc_credit_id and amount > 0:
                lines_data = [
                    {'account_id': acc_debit_id, 'entry_type': 'DEBIT', 'amount': amount, 'description': narration},
                    {'account_id': acc_credit_id, 'entry_type': 'CREDIT', 'amount': amount, 'description': narration}
                ]
        else:
            for idx in line_indices:
                acc_id = request.POST.get(f'account_{idx}')
                etype = request.POST.get(f'entry_type_{idx}')
                amt = Decimal(request.POST.get(f'amount_{idx}', '0.00') or '0.00')
                desc = request.POST.get(f'description_{idx}', '')
                if acc_id and amt > 0 and etype:
                    lines_data.append({
                        'account_id': acc_id,
                        'entry_type': etype,
                        'amount': amt,
                        'description': desc
                    })

        try:
            voucher = create_and_post_voucher(
                company=company,
                voucher_type=v_type,
                date=date_val,
                narration=narration,
                lines_data=lines_data,
                user=request.user,
                reference_number=ref_num,
                party_type=party_type,
                party_id=str(party_id),
                party_name=party_name,
                auto_set_off=auto_set_off
            )
            messages.success(request, f"Voucher {voucher.voucher_number} posted successfully to the General Ledger.")
            return redirect('accounting:voucher_detail', voucher_id=voucher.id)
        except Exception as e:
            messages.error(request, f"Failed to create voucher: {e}")

    return render(request, 'accounting/voucher_form.html', {
        'accounts': accounts,
        'customers': customers,
        'bank_accounts': bank_accounts,
        'voucher_types': Voucher.VOUCHER_TYPE_CHOICES,
        'party_types': Voucher.PARTY_TYPE_CHOICES,
        'today': timezone.now().date(),
        'active_tab': 'accounting',
        'active_sub': 'vouchers'
    })


@login_required
@require_permission('accounting', 'view')
def voucher_detail(request, voucher_id):
    company = request.company
    voucher = get_object_or_404(Voucher, id=voucher_id, company=company)
    return render(request, 'accounting/voucher_detail.html', {
        'voucher': voucher,
        'lines': voucher.lines.all(),
        'active_tab': 'accounting',
        'active_sub': 'vouchers'
    })


@login_required
@require_permission('accounting', 'edit')
def voucher_cancel(request, voucher_id):
    company = request.company
    voucher = get_object_or_404(Voucher, id=voucher_id, company=company)
    if voucher.status == 'CANCELLED':
        messages.warning(request, "Voucher is already cancelled.")
    else:
        if voucher.journal_entry:
            reverse_journal_entry(voucher.journal_entry, request.user, reason=f"Cancelled Voucher {voucher.voucher_number}")
        voucher.status = 'CANCELLED'
        voucher.save(update_fields=['status'])
        messages.success(request, f"Voucher {voucher.voucher_number} has been cancelled and reversed in the GL.")

    return redirect('accounting:voucher_detail', voucher_id=voucher.id)


# ==============================================================================
# 4. MANUAL JOURNALS
# ==============================================================================

@login_required
@require_permission('accounting', 'view')
def journal_list(request):
    company = request.company
    journals = JournalEntry.objects.filter(company=company).order_by('-date', '-id')
    return render(request, 'accounting/journal_list.html', {
        'journals': journals,
        'active_tab': 'accounting',
        'active_sub': 'journals'
    })


@login_required
@require_permission('accounting', 'create')
def journal_create(request):
    company = request.company
    ensure_default_chart_of_accounts(company)
    accounts = Account.objects.filter(company=company, is_active=True).order_by('code')

    if request.method == 'POST':
        count = JournalEntry.objects.filter(company=company).count() + 1
        date_str = timezone.now().strftime('%Y%m%d')
        je_num = f"JE-{date_str}-{count:04d}"

        date_val = request.POST.get('date', timezone.now().date())
        ref = request.POST.get('reference', '').strip()
        desc = request.POST.get('description', 'Manual Journal Entry').strip()

        je = JournalEntry.objects.create(
            company=company,
            entry_number=je_num,
            date=date_val,
            reference=ref,
            description=desc,
            source_module='MANUAL'
        )

        # Parse debit/credit lines
        line_indices = request.POST.getlist('line_index')
        if not line_indices:
            acc1_id = request.POST.get('account_1')
            dr1 = Decimal(request.POST.get('debit_1', '0.00') or '0.00')
            cr1 = Decimal(request.POST.get('credit_1', '0.00') or '0.00')

            acc2_id = request.POST.get('account_2')
            dr2 = Decimal(request.POST.get('debit_2', '0.00') or '0.00')
            cr2 = Decimal(request.POST.get('credit_2', '0.00') or '0.00')

            if acc1_id and (dr1 > 0 or cr1 > 0):
                acc1 = get_object_or_404(Account, id=acc1_id, company=company)
                JournalLine.objects.create(journal=je, account=acc1, debit=dr1, credit=cr1, description="Line 1")

            if acc2_id and (dr2 > 0 or cr2 > 0):
                acc2 = get_object_or_404(Account, id=acc2_id, company=company)
                JournalLine.objects.create(journal=je, account=acc2, debit=dr2, credit=cr2, description="Line 2")
        else:
            for idx in line_indices:
                acc_id = request.POST.get(f'account_{idx}')
                dr = Decimal(request.POST.get(f'debit_{idx}', '0.00') or '0.00')
                cr = Decimal(request.POST.get(f'credit_{idx}', '0.00') or '0.00')
                line_desc = request.POST.get(f'description_{idx}', '')
                if acc_id and (dr > 0 or cr > 0):
                    acc = get_object_or_404(Account, id=acc_id, company=company)
                    JournalLine.objects.create(journal=je, account=acc, debit=dr, credit=cr, description=line_desc)

        auto_post = request.POST.get('auto_post') == 'on'
        if auto_post:
            try:
                post_journal_entry(je, request.user)
                messages.success(request, f"Journal Entry {je.entry_number} posted successfully.")
            except Exception as e:
                messages.warning(request, f"Journal saved as DRAFT: {e}")
        else:
            messages.success(request, f"Draft Journal Entry {je.entry_number} saved.")

        return redirect('accounting:journal_detail', journal_id=je.id)

    return render(request, 'accounting/journal_form.html', {
        'accounts': accounts,
        'today': timezone.now().date(),
        'active_tab': 'accounting',
        'active_sub': 'journals'
    })


@login_required
@require_permission('accounting', 'view')
def journal_detail(request, journal_id):
    company = request.company
    journal = get_object_or_404(JournalEntry, id=journal_id, company=company)
    return render(request, 'accounting/journal_detail.html', {
        'journal': journal,
        'lines': journal.lines.all(),
        'active_tab': 'accounting',
        'active_sub': 'journals'
    })


@login_required
@require_permission('accounting', 'edit')
def journal_reverse(request, journal_id):
    company = request.company
    journal = get_object_or_404(JournalEntry, id=journal_id, company=company)
    try:
        rev_je = reverse_journal_entry(journal, request.user, reason="User Reversal Action")
        messages.success(request, f"Journal {journal.entry_number} reversed by {rev_je.entry_number}.")
    except Exception as e:
        messages.error(request, f"Failed to reverse journal: {e}")

    return redirect('accounting:journal_detail', journal_id=journal.id)


# ==============================================================================
# 5. BANKING & CASH ACCOUNTS
# ==============================================================================

@login_required
@require_permission('accounting', 'view')
def banking_overview(request):
    company = request.company
    ensure_default_chart_of_accounts(company)

    bank_accounts = BankAccount.objects.filter(company=company)
    total_liquidity = sum((b.current_balance for b in bank_accounts.filter(status='ACTIVE')), Decimal('0.00'))

    recent_txns = BankTransaction.objects.filter(
        bank_account__company=company
    ).select_related('bank_account').order_by('-date', '-id')[:20]

    all_gl_bank_accounts = Account.objects.filter(company=company, sub_category__in=['BANK', 'CASH'])

    return render(request, 'accounting/banking_overview.html', {
        'bank_accounts': bank_accounts,
        'total_liquidity': total_liquidity,
        'recent_transactions': recent_txns,
        'gl_accounts': all_gl_bank_accounts,
        'active_tab': 'accounting',
        'active_sub': 'banking'
    })


@login_required
@require_permission('accounting', 'create')
def bank_account_create(request):
    company = request.company
    if request.method == 'POST':
        bank_name = request.POST.get('bank_name', '').strip()
        account_name = request.POST.get('account_name', '').strip()
        account_number = request.POST.get('account_number', '').strip()
        account_type = request.POST.get('account_type', 'BANK')
        ifsc_code = request.POST.get('ifsc_code', '').strip()
        branch_name = request.POST.get('branch_name', '').strip()
        gl_account_id = request.POST.get('gl_account_id')
        opening_balance = Decimal(request.POST.get('opening_balance', '0.00') or '0.00')
        description = request.POST.get('description', '').strip()

        gl_acc = Account.objects.filter(id=gl_account_id, company=company).first() if gl_account_id else None

        if not account_name or not account_number:
            messages.error(request, "Account Name and Account Number are required.")
            return redirect('accounting:banking_overview')

        b_acc = BankAccount.objects.create(
            company=company,
            bank_name=bank_name,
            account_name=account_name,
            account_number=account_number,
            account_type=account_type,
            ifsc_code=ifsc_code,
            branch_name=branch_name,
            gl_account=gl_acc,
            opening_balance=opening_balance,
            current_balance=opening_balance,
            status='ACTIVE',
            description=description
        )
        messages.success(request, f"Bank Account '{b_acc.account_name}' created successfully.")

    return redirect('accounting:banking_overview')


@login_required
@require_permission('accounting', 'create')
def bank_transfer_contra(request):
    """
    Direct Contra Transfer between Bank <-> Cash accounts.
    Posts balanced Contra Voucher (CN-...) and synchronizes both accounts!
    """
    company = request.company
    if request.method == 'POST':
        from_bank_id = request.POST.get('from_bank_id')
        to_bank_id = request.POST.get('to_bank_id')
        amount = Decimal(request.POST.get('amount', '0.00') or '0.00')
        date_val = request.POST.get('date', timezone.now().date())
        ref = request.POST.get('reference', '').strip()
        narration = request.POST.get('narration', 'Contra Inter-Account Transfer').strip()

        if from_bank_id == to_bank_id:
            messages.error(request, "Source and destination accounts must be different.")
            return redirect('accounting:banking_overview')

        from_bank = get_object_or_404(BankAccount, id=from_bank_id, company=company)
        to_bank = get_object_or_404(BankAccount, id=to_bank_id, company=company)

        if not from_bank.gl_account or not to_bank.gl_account:
            messages.error(request, "Both bank accounts must be linked to General Ledger accounts.")
            return redirect('accounting:banking_overview')

        lines = [
            {'account_id': to_bank.gl_account.id, 'entry_type': 'DEBIT', 'amount': amount, 'description': f"Transfer received from {from_bank.account_name}"},
            {'account_id': from_bank.gl_account.id, 'entry_type': 'CREDIT', 'amount': amount, 'description': f"Transfer to {to_bank.account_name}"}
        ]

        try:
            voucher = create_and_post_voucher(
                company=company,
                voucher_type='CONTRA',
                date=date_val,
                narration=narration,
                lines_data=lines,
                user=request.user,
                reference_number=ref,
                party_type='OTHER',
                party_name=f"{from_bank.account_name} -> {to_bank.account_name}"
            )
            messages.success(request, f"Contra Transfer of ₹{amount} posted via Voucher {voucher.voucher_number}.")
        except Exception as e:
            messages.error(request, f"Transfer failed: {e}")

    return redirect('accounting:banking_overview')


@login_required
@require_permission('accounting', 'view')
def bank_account_detail(request, account_id):
    company = request.company
    bank_account = get_object_or_404(BankAccount, id=account_id, company=company)
    transactions = bank_account.transactions.all().order_by('-date', '-id')

    return render(request, 'accounting/bank_account_detail.html', {
        'bank_account': bank_account,
        'transactions': transactions,
        'active_tab': 'accounting',
        'active_sub': 'banking'
    })


# ==============================================================================
# 6. FIXED ASSETS & DEPRECIATION SCHEDULE
# ==============================================================================

@login_required
@require_permission('accounting', 'view')
def fixed_assets_list(request):
    company = request.company
    ensure_default_chart_of_accounts(company)

    assets = FixedAsset.objects.filter(company=company).order_by('name')

    total_cost = sum((a.purchase_cost for a in assets), Decimal('0.00'))
    total_depreciation = sum((a.accumulated_depreciation for a in assets), Decimal('0.00'))
    total_book_value = total_cost - total_depreciation

    fixed_asset_gl_accounts = Account.objects.filter(company=company, sub_category='FIXED_ASSET')
    accum_depr_gl_accounts = Account.objects.filter(company=company, sub_category='ACCUMULATED_DEPRECIATION')
    depr_expense_gl_accounts = Account.objects.filter(company=company, sub_category='DEPRECIATION_EXPENSE')

    return render(request, 'accounting/fixed_assets_list.html', {
        'assets': assets,
        'total_cost': total_cost,
        'total_depreciation': total_depreciation,
        'total_book_value': total_book_value,
        'fixed_asset_gl_accounts': fixed_asset_gl_accounts,
        'accum_depr_gl_accounts': accum_depr_gl_accounts,
        'depr_expense_gl_accounts': depr_expense_gl_accounts,
        'active_tab': 'accounting',
        'active_sub': 'assets'
    })


@login_required
@require_permission('accounting', 'create')
def fixed_asset_create(request):
    company = request.company
    if request.method == 'POST':
        code = request.POST.get('asset_code', '').strip()
        name = request.POST.get('name', '').strip()
        category = request.POST.get('category', 'IT Equipment').strip()
        location = request.POST.get('location', 'Head Office').strip()
        serial_num = request.POST.get('serial_number', '').strip()
        acq_date = request.POST.get('acquisition_date', timezone.now().date())
        purchase_cost = Decimal(request.POST.get('purchase_cost', '0.00') or '0.00')
        salvage_value = Decimal(request.POST.get('salvage_value', '0.00') or '0.00')
        useful_life = int(request.POST.get('useful_life_months', '36') or 36)

        asset_acc_id = request.POST.get('asset_account')
        accum_acc_id = request.POST.get('accumulated_depreciation_account')
        depr_exp_acc_id = request.POST.get('depreciation_account')

        asset_acc = get_object_or_404(Account, id=asset_acc_id, company=company)
        accum_acc = Account.objects.filter(id=accum_acc_id, company=company).first() or get_account_by_code(company, '1550')
        depr_exp_acc = Account.objects.filter(id=depr_exp_acc_id, company=company).first() or get_account_by_code(company, '5090')

        asset = FixedAsset.objects.create(
            company=company,
            asset_code=code,
            name=name,
            category=category,
            location=location,
            serial_number=serial_num,
            acquisition_date=acq_date,
            purchase_cost=purchase_cost,
            salvage_value=salvage_value,
            useful_life_months=useful_life,
            asset_account=asset_acc,
            accumulated_depreciation_account=accum_acc,
            depreciation_account=depr_exp_acc,
            status='ACTIVE'
        )
        messages.success(request, f"Fixed Asset {asset.asset_code} ({asset.name}) added successfully.")

    return redirect('accounting:fixed_assets_list')


@login_required
@require_permission('accounting', 'view')
def fixed_asset_detail(request, asset_id):
    company = request.company
    asset = get_object_or_404(FixedAsset, id=asset_id, company=company)

    # Generate depreciation schedule projection
    monthly_depr = asset.monthly_depreciation_amount()
    schedule_rows = []
    accum = Decimal('0.00')
    curr_cost = asset.purchase_cost

    for i in range(1, min(asset.useful_life_months + 1, 120)):
        accum += monthly_depr
        if accum > (asset.purchase_cost - asset.salvage_value):
            accum = asset.purchase_cost - asset.salvage_value
        bv = max(Decimal('0.00'), curr_cost - accum)
        schedule_rows.append({
            'period': i,
            'depreciation': monthly_depr,
            'accumulated': accum,
            'book_value': bv,
            'is_posted': accum <= asset.accumulated_depreciation
        })

    posted_entries = asset.depreciation_entries.all().order_by('-period_index')

    return render(request, 'accounting/fixed_asset_detail.html', {
        'asset': asset,
        'schedule_rows': schedule_rows,
        'posted_entries': posted_entries,
        'active_tab': 'accounting',
        'active_sub': 'assets'
    })


@login_required
@require_permission('accounting', 'edit')
def fixed_asset_post_depreciation(request, asset_id):
    company = request.company
    asset = get_object_or_404(FixedAsset, id=asset_id, company=company)
    try:
        sched = post_asset_depreciation(asset, request.user)
        messages.success(request, f"Depreciation for period #{sched.period_index} (₹{sched.depreciation_amount}) posted to General Ledger.")
    except Exception as e:
        messages.error(request, f"Failed to post depreciation: {e}")

    return redirect('accounting:fixed_asset_detail', asset_id=asset.id)


# ==============================================================================
# 7. TAX MANAGEMENT
# ==============================================================================

@login_required
@require_permission('accounting', 'view')
def tax_management(request):
    company = request.company
    ensure_default_chart_of_accounts(company)

    taxes = TaxRate.objects.filter(company=company).order_by('tax_type', 'rate')
    tax_gl_accounts = Account.objects.filter(company=company, sub_category__in=['OUTPUT_TAX', 'INPUT_TAX', 'STATUTORY_DUES'])

    return render(request, 'accounting/tax_management.html', {
        'taxes': taxes,
        'tax_gl_accounts': tax_gl_accounts,
        'active_tab': 'accounting',
        'active_sub': 'taxes'
    })


@login_required
@require_permission('accounting', 'create')
def tax_create(request):
    company = request.company
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        rate = Decimal(request.POST.get('rate', '0.00') or '0.00')
        tax_type = request.POST.get('tax_type', 'GST')
        gl_account_id = request.POST.get('gl_account_id')

        gl_acc = Account.objects.filter(id=gl_account_id, company=company).first() if gl_account_id else None

        if name and rate >= 0:
            TaxRate.objects.create(
                company=company,
                name=name,
                rate=rate,
                tax_type=tax_type,
                gl_account=gl_acc,
                is_active=True
            )
            messages.success(request, f"Tax Rate '{name}' created successfully.")

    return redirect('accounting:tax_management')


@login_required
@require_permission('accounting', 'edit')
def tax_toggle(request, tax_id):
    company = request.company
    tax = get_object_or_404(TaxRate, id=tax_id, company=company)
    tax.is_active = not tax.is_active
    tax.save(update_fields=['is_active'])
    status_str = "activated" if tax.is_active else "deactivated"
    messages.success(request, f"Tax Rate '{tax.name}' {status_str}.")
    return redirect('accounting:tax_management')


# ==============================================================================
# 8. FINANCIAL STATEMENTS & REPORTING (Trial Balance, P&L, Balance Sheet, Banking)
# ==============================================================================

@login_required
@require_permission('accounting', 'view')
def financial_reports(request):
    company = request.company
    ensure_default_chart_of_accounts(company)

    report_type = request.GET.get('report', 'pl').lower()
    date_preset = request.GET.get('preset', 'year')
    start_date = request.GET.get('start_date', '')
    end_date = request.GET.get('end_date', '')

    today = timezone.now().date()
    if date_preset == 'month':
        start_date = today.replace(day=1)
        end_date = today
    elif date_preset == 'quarter':
        quarter_month = ((today.month - 1) // 3) * 3 + 1
        start_date = today.replace(month=quarter_month, day=1)
        end_date = today
    elif date_preset == 'year':
        # Indian Financial Year starts April 1
        fy_year = today.year if today.month >= 4 else today.year - 1
        start_date = today.replace(year=fy_year, month=4, day=1)
        end_date = today

    statements = generate_financial_statements(company, start_date=start_date if str(start_date) else None, end_date=end_date if str(end_date) else None)

    return render(request, 'accounting/financial_reports.html', {
        'report_type': report_type,
        'date_preset': date_preset,
        'start_date': start_date,
        'end_date': end_date,
        'statements': statements,
        'active_tab': 'accounting',
        'active_sub': 'reports'
    })


@login_required
@require_permission('accounting', 'view')
def financial_reports_export(request, report_type):
    company = request.company
    statements = generate_financial_statements(company)

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{report_type.upper()}_Report_{company.code}.csv"'

    writer = csv.writer(response)

    if report_type == 'trial_balance':
        writer.writerow(['Account Code', 'Account Name', 'Category', 'Debit (INR)', 'Credit (INR)', 'Net Balance (INR)'])
        for item in statements['trial_balance']:
            writer.writerow([
                item['account'].code,
                item['account'].name,
                item['account'].get_account_type_display(),
                item['debit'],
                item['credit'],
                item['balance']
            ])
        writer.writerow([])
        writer.writerow(['TOTALS', '', '', statements['total_tb_debit'], statements['total_tb_credit'], 'BALANCED' if statements['tb_is_balanced'] else 'UNBALANCED'])

    elif report_type == 'pl':
        writer.writerow(['Profit & Loss Statement for', company.name])
        writer.writerow([])
        writer.writerow(['Line Item', 'Amount (INR)'])
        writer.writerow(['Operating Revenue', statements['operating_revenue']])
        writer.writerow(['Other Income', statements['other_income']])
        writer.writerow(['TOTAL REVENUE', statements['total_revenue']])
        writer.writerow(['Cost of Sales / Direct Cost', statements['cost_of_sales']])
        writer.writerow(['GROSS PROFIT', statements['gross_profit']])
        writer.writerow(['Gross Margin %', f"{statements['gross_margin_pct']}%"])
        writer.writerow([])
        writer.writerow(['OPERATING EXPENSES', ''])
        writer.writerow(['Salaries & Benefits', statements['salary_expenses']])
        writer.writerow(['Rent & Workspace', statements['rent_expenses']])
        writer.writerow(['Financial & Bank Fees', statements['financial_expenses']])
        writer.writerow(['Other Operating Expenses', statements['other_expenses']])
        writer.writerow(['TOTAL OPERATING EXPENSES', statements['operating_expenses']])
        writer.writerow(['EBITDA / Operating Income', statements['ebitda']])
        writer.writerow(['Depreciation Expense', statements['depreciation_expenses']])
        writer.writerow(['NET PROFIT / LOSS', statements['net_profit']])
        writer.writerow(['Net Margin %', f"{statements['net_margin_pct']}%"])

    elif report_type == 'balance_sheet':
        writer.writerow(['Balance Sheet for', company.name])
        writer.writerow([])
        writer.writerow(['ASSETS', 'Amount (INR)'])
        writer.writerow(['Cash & Bank Accounts', statements['cash_and_bank']])
        writer.writerow(['Accounts Receivable', statements['accounts_receivable']])
        writer.writerow(['Inventory', statements['inventory']])
        writer.writerow(['Current Assets Subtotal', statements['current_assets']])
        writer.writerow(['Fixed Assets (Gross)', statements['fixed_assets_gross']])
        writer.writerow(['Less: Accumulated Depreciation', statements['accumulated_depreciation']])
        writer.writerow(['Net Fixed Assets', statements['net_fixed_assets']])
        writer.writerow(['TOTAL ASSETS', statements['total_assets']])
        writer.writerow([])
        writer.writerow(['LIABILITIES', 'Amount (INR)'])
        writer.writerow(['Accounts Payable', statements['accounts_payable']])
        writer.writerow(['Taxes Payable (GST/TDS)', statements['taxes_payable']])
        writer.writerow(['Current Liabilities Subtotal', statements['current_liabilities']])
        writer.writerow(['Long Term Liabilities', statements['long_term_liabilities']])
        writer.writerow(['TOTAL LIABILITIES', statements['total_liabilities']])
        writer.writerow([])
        writer.writerow(['EQUITY', 'Amount (INR)'])
        writer.writerow(['Owner Equity / Capital', statements['owner_equity']])
        writer.writerow(['Retained Earnings', statements['retained_earnings']])
        writer.writerow(['Current Period Net Profit', statements['current_period_profit']])
        writer.writerow(['TOTAL EQUITY', statements['total_equity']])
        writer.writerow([])
        writer.writerow(['TOTAL LIABILITIES & EQUITY', statements['total_liabilities_and_equity']])
        writer.writerow(['Balance Check', 'MATCHED' if statements['bs_is_balanced'] else 'DIFFERENCE'])

    return response
