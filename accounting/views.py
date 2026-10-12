from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from decimal import Decimal
from django.utils import timezone
from accounting.models import Account, JournalEntry, JournalLine
from accounting.services import (
    ensure_default_chart_of_accounts,
    post_journal_entry,
    reverse_journal_entry
)
from tenants.decorators import require_permission

@login_required
@require_permission('accounting', 'view')
def journal_list(request):
    company = request.company
    journals = JournalEntry.objects.filter(company=company)
    return render(request, 'accounting/journal_list.html', {
        'journals': journals,
        'active_tab': 'accounting',
        'active_sub': 'journals'
    })


@login_required
@require_permission('accounting', 'create')
def journal_create(request):
    company = request.company
    accounts = Account.objects.filter(company=company, is_active=True)

    if request.method == 'POST':
        count = JournalEntry.objects.filter(company=company).count() + 1
        je_num = f"JE-{10000 + count}"

        je = JournalEntry.objects.create(
            company=company,
            entry_number=je_num,
            date=request.POST.get('date', timezone.now().date()),
            reference=request.POST.get('reference', ''),
            description=request.POST.get('description', 'Manual Journal Entry'),
            source_module='MANUAL'
        )

        acc1_id = request.POST.get('account_1')
        dr1 = Decimal(request.POST.get('debit_1', '0.00'))
        cr1 = Decimal(request.POST.get('credit_1', '0.00'))

        acc2_id = request.POST.get('account_2')
        dr2 = Decimal(request.POST.get('debit_2', '0.00'))
        cr2 = Decimal(request.POST.get('credit_2', '0.00'))

        if acc1_id and (dr1 > 0 or cr1 > 0):
            acc1 = get_object_or_404(Account, id=acc1_id, company=company)
            JournalLine.objects.create(journal=je, account=acc1, debit=dr1, credit=cr1, description="Line 1")

        if acc2_id and (dr2 > 0 or cr2 > 0):
            acc2 = get_object_or_404(Account, id=acc2_id, company=company)
            JournalLine.objects.create(journal=je, account=acc2, debit=dr2, credit=cr2, description="Line 2")

        auto_post = request.POST.get('auto_post') == 'on'
        if auto_post:
            try:
                post_journal_entry(je, request.user)
                messages.success(request, f"Journal Entry {je.entry_number} posted successfully.")
            except Exception as e:
                messages.warning(request, f"Journal created as DRAFT: {e}")
        else:
            messages.success(request, f"Draft Journal Entry {je.entry_number} saved.")

        return redirect('accounting:journal_detail', journal_id=je.id)

    return render(request, 'accounting/journal_form.html', {
        'accounts': accounts,
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


@login_required
@require_permission('accounting', 'view')
def chart_of_accounts(request):
    company = request.company
    ensure_default_chart_of_accounts(company)
    
    type_filter = request.GET.get('type', '')
    query = request.GET.get('q', '')

    accounts = Account.objects.filter(company=company)
    if type_filter:
        accounts = accounts.filter(account_type=type_filter)
    if query:
        accounts = accounts.filter(models.Q(name__icontains=query) | models.Q(code__icontains=query))

    account_list = []
    category_totals = {'ASSET': Decimal('0.00'), 'LIABILITY': Decimal('0.00'), 'EQUITY': Decimal('0.00'), 'INCOME': Decimal('0.00'), 'EXPENSE': Decimal('0.00')}

    for acc in accounts:
        bal = acc.current_balance()
        account_list.append({
            'account': acc,
            'balance': bal
        })
        if acc.account_type in category_totals:
            category_totals[acc.account_type] += bal

    return render(request, 'accounting/chart_of_accounts.html', {
        'accounts_with_balance': account_list,
        'category_totals': category_totals,
        'type_filter': type_filter,
        'query': query,
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
        opening_balance = Decimal(request.POST.get('opening_balance', '0.00'))
        description = request.POST.get('description', '').strip()

        if not code or not name:
            messages.error(request, "Account Code and Name are required fields.")
            return redirect('accounting:chart_of_accounts')

        if Account.objects.filter(company=company, code=code).exists():
            messages.error(request, f"Account Code '{code}' already exists in your Chart of Accounts.")
            return redirect('accounting:chart_of_accounts')

        acc = Account.objects.create(
            company=company,
            code=code,
            name=name,
            account_type=account_type,
            opening_balance=opening_balance,
            description=description,
            is_system=False
        )

        messages.success(request, f"New Account #{acc.code} ({acc.name}) created successfully.")
        return redirect('accounting:chart_of_accounts')

    return redirect('accounting:chart_of_accounts')


@login_required
@require_permission('accounting', 'edit')
def account_edit(request, account_id):
    company = request.company
    account = get_object_or_404(Account, id=account_id, company=company)
    
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        if name:
            account.name = name
            account.description = description
            account.save()
            messages.success(request, f"Account #{account.code} updated successfully.")
        return redirect('accounting:chart_of_accounts')

    return redirect('accounting:chart_of_accounts')


@login_required
@require_permission('accounting', 'view')
def financial_reports(request):
    company = request.company
    ensure_default_chart_of_accounts(company)
    accounts = Account.objects.filter(company=company)

    total_assets = Decimal('0.00')
    total_liabilities = Decimal('0.00')
    total_equity = Decimal('0.00')
    total_income = Decimal('0.00')
    total_expenses = Decimal('0.00')

    trial_balance = []
    for acc in accounts:
        bal = acc.current_balance()
        trial_balance.append({'account': acc, 'balance': bal})
        
        if acc.account_type == 'ASSET':
            total_assets += bal
        elif acc.account_type == 'LIABILITY':
            total_liabilities += bal
        elif acc.account_type == 'EQUITY':
            total_equity += bal
        elif acc.account_type == 'INCOME':
            total_income += bal
        elif acc.account_type == 'EXPENSE':
            total_expenses += bal

    net_profit = total_income - total_expenses

    return render(request, 'accounting/financial_reports.html', {
        'trial_balance': trial_balance,
        'total_assets': total_assets,
        'total_liabilities': total_liabilities,
        'total_equity': total_equity,
        'total_income': total_income,
        'total_expenses': total_expenses,
        'net_profit': net_profit,
        'active_tab': 'accounting',
        'active_sub': 'reports'
    })
