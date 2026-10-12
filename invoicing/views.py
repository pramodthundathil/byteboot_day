from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from decimal import Decimal
from django.utils import timezone
from invoicing.models import Invoice, InvoiceLine, CustomerPayment, PaymentAllocation, PaymentReceipt
from crm.models import Customer
from accounting.services import post_invoice_to_ledger, post_payment_to_ledger
from invoicing.services import process_and_allocate_payment
from tenants.decorators import require_permission

@login_required
@require_permission('invoicing', 'view')
def invoice_list(request):
    company = request.company
    invoices = Invoice.objects.filter(company=company)
    status_filter = request.GET.get('status', '')
    if status_filter:
        invoices = invoices.filter(status=status_filter)

    return render(request, 'invoicing/invoice_list.html', {
        'invoices': invoices,
        'status_filter': status_filter,
        'active_tab': 'invoicing',
        'active_sub': 'invoices'
    })


@login_required
@require_permission('invoicing', 'create')
def invoice_create(request):
    company = request.company
    customers = Customer.objects.filter(company=company)

    if request.method == 'POST':
        cust_id = request.POST.get('customer_id')
        customer = get_object_or_404(Customer, id=cust_id, company=company)
        
        count = Invoice.objects.filter(company=company).count() + 1
        inv_num = f"INV-{1000 + count}"
        
        is_interstate = request.POST.get('is_interstate') == 'on' or (customer.state and customer.state.lower() != 'kerala')

        invoice = Invoice.objects.create(
            company=company,
            invoice_number=inv_num,
            customer=customer,
            invoice_type='TAX',
            date=request.POST.get('date', timezone.now().date()),
            due_date=request.POST.get('due_date', timezone.now().date()),
            place_of_supply=customer.state or 'Kerala',
            is_interstate=is_interstate,
            notes=request.POST.get('notes', 'Thank you for your business!'),
            payment_terms="Payment due within 30 days.",
            bank_details="Bank: HDFC Bank | A/C: 50200012345678 | IFSC: HDFC0001234"
        )

        desc = request.POST.get('description', 'Software Development Services')
        rate = Decimal(request.POST.get('rate', '50000.00'))
        qty = Decimal(request.POST.get('quantity', '1.00'))
        
        line = InvoiceLine.objects.create(
            invoice=invoice,
            description=desc,
            hsn_sac_code="998314",
            quantity=qty,
            rate=rate
        )
        invoice.update_totals_from_lines()

        messages.success(request, f"Draft Tax Invoice {invoice.invoice_number} created.")
        return redirect('invoicing:invoice_detail', invoice_id=invoice.id)

    return render(request, 'invoicing/invoice_form.html', {
        'customers': customers,
        'active_tab': 'invoicing',
        'active_sub': 'invoices'
    })


@login_required
@require_permission('invoicing', 'view')
def invoice_detail(request, invoice_id):
    company = request.company
    invoice = get_object_or_404(Invoice, id=invoice_id, company=company)
    return render(request, 'invoicing/invoice_detail.html', {
        'invoice': invoice,
        'lines': invoice.lines.all(),
        'allocations': invoice.allocations.all(),
        'active_tab': 'invoicing',
        'active_sub': 'invoices'
    })


@login_required
@require_permission('invoicing', 'edit')
def invoice_post(request, invoice_id):
    company = request.company
    invoice = get_object_or_404(Invoice, id=invoice_id, company=company)
    
    try:
        je = post_invoice_to_ledger(invoice, request.user)
        messages.success(request, f"Invoice {invoice.invoice_number} posted to General Ledger (#{je.entry_number}).")
    except Exception as e:
        messages.error(request, f"Failed to post invoice: {e}")

    return redirect('invoicing:invoice_detail', invoice_id=invoice.id)


@login_required
@require_permission('invoicing', 'create')
def record_invoice_payment(request, invoice_id):
    company = request.company
    invoice = get_object_or_404(Invoice, id=invoice_id, company=company)

    if request.method == 'POST':
        amount = Decimal(request.POST.get('amount', str(invoice.balance_due)))
        method = request.POST.get('payment_method', 'BANK_TRANSFER')
        ref = request.POST.get('reference_number', '')
        pay_date = request.POST.get('payment_date', timezone.now().date())

        if amount <= Decimal('0.00'):
            messages.error(request, "Payment amount must be greater than zero.")
            return redirect('invoicing:invoice_detail', invoice_id=invoice.id)

        if amount > invoice.balance_due:
            messages.warning(request, f"Entered amount ₹{amount} exceeds balance due ₹{invoice.balance_due}. Payment capped at ₹{invoice.balance_due}.")
            amount = invoice.balance_due

        if invoice.status == 'DRAFT':
            try:
                post_invoice_to_ledger(invoice, request.user)
            except Exception:
                pass

        count = CustomerPayment.objects.filter(company=company).count() + 1
        pay_num = f"PAY-{1000 + count}"

        payment = CustomerPayment.objects.create(
            company=company,
            payment_number=pay_num,
            customer=invoice.customer,
            payment_date=pay_date,
            amount=amount,
            unallocated_amount=amount,
            payment_method=method,
            reference_number=ref
        )

        process_and_allocate_payment(payment, invoice, amount, request.user)
        messages.success(request, f"Payment {payment.payment_number} of ₹{amount} recorded for Invoice #{invoice.invoice_number}. Invoice status: {invoice.get_status_display()}.")

        return redirect('invoicing:invoice_detail', invoice_id=invoice.id)

    return redirect('invoicing:invoice_detail', invoice_id=invoice.id)


@login_required
@require_permission('invoicing', 'view')
def invoice_print(request, invoice_id):
    company = request.company
    invoice = get_object_or_404(Invoice, id=invoice_id, company=company)
    return render(request, 'invoicing/invoice_printable.html', {
        'company': company,
        'invoice': invoice,
        'lines': invoice.lines.all()
    })


@login_required
@require_permission('invoicing', 'view')
def payment_list(request):
    company = request.company
    payments = CustomerPayment.objects.filter(company=company)
    return render(request, 'invoicing/payment_list.html', {
        'payments': payments,
        'active_tab': 'invoicing',
        'active_sub': 'payments'
    })


@login_required
@require_permission('invoicing', 'create')
def payment_create(request):
    company = request.company
    customers = Customer.objects.filter(company=company)
    invoices = Invoice.objects.filter(company=company, status__in=['ISSUED', 'PARTIALLY_PAID'])

    if request.method == 'POST':
        cust_id = request.POST.get('customer_id')
        customer = get_object_or_404(Customer, id=cust_id, company=company)
        
        amount = Decimal(request.POST.get('amount', '0.00'))
        method = request.POST.get('payment_method', 'BANK_TRANSFER')
        ref = request.POST.get('reference_number', '')

        count = CustomerPayment.objects.filter(company=company).count() + 1
        pay_num = f"PAY-{1000 + count}"

        payment = CustomerPayment.objects.create(
            company=company,
            payment_number=pay_num,
            customer=customer,
            payment_date=request.POST.get('payment_date', timezone.now().date()),
            amount=amount,
            unallocated_amount=amount,
            payment_method=method,
            reference_number=ref
        )

        inv_id = request.POST.get('invoice_id')
        if inv_id:
            inv = get_object_or_404(Invoice, id=inv_id, company=company)
            alloc_amt = min(amount, inv.balance_due)
            process_and_allocate_payment(payment, inv, alloc_amt, request.user)
            messages.success(request, f"Payment {payment.payment_number} recorded & allocated ₹{alloc_amt} to Invoice {inv.invoice_number}.")
        else:
            post_payment_to_ledger(payment, request.user)
            messages.success(request, f"Payment {payment.payment_number} recorded.")

        return redirect('invoicing:payment_list')

    return render(request, 'invoicing/payment_form.html', {
        'customers': customers,
        'invoices': invoices,
        'active_tab': 'invoicing',
        'active_sub': 'payments'
    })
