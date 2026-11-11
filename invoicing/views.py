import json
from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.db import transaction

from invoicing.models import (
    Invoice, InvoiceLine, Quotation, CustomerPayment, PaymentAllocation, PaymentReceipt,
    Product, Vendor, VendorBill, VendorBillLine, PaymentMade, BillPaymentAllocation
)
from crm.models import Customer
from accounting.models import Account, BankAccount, Voucher, JournalEntry
from accounting.services import post_invoice_to_ledger, post_payment_to_ledger, get_account_by_code
from invoicing.services import (
    process_and_allocate_payment, post_bill_to_ledger,
    post_payment_made_to_ledger, process_and_allocate_vendor_payment,
    auto_allocate_vendor_payment_fifo
)
from tenants.decorators import require_permission


# ==============================================================================
# INVOICE MANAGEMENT (SALES)
# ==============================================================================

@login_required
@require_permission('invoicing', 'view')
def invoice_list(request):
    company = request.company
    invoices = Invoice.objects.filter(company=company)
    status_filter = request.GET.get('status', '')
    if status_filter:
        invoices = invoices.filter(status=status_filter)

    total_receivable = sum((inv.balance_due for inv in invoices.filter(status__in=['ISSUED', 'PARTIALLY_PAID'])), Decimal('0.00'))
    total_billed = sum((inv.grand_total for inv in invoices.exclude(status='CANCELLED')), Decimal('0.00'))

    return render(request, 'invoicing/invoice_list.html', {
        'invoices': invoices,
        'status_filter': status_filter,
        'total_receivable': total_receivable,
        'total_billed': total_billed,
        'active_tab': 'invoicing',
        'active_sub': 'invoices'
    })


def safe_decimal(val, default='0.00'):
    try:
        val_str = str(val).strip() if val is not None else ''
        return Decimal(val_str) if val_str != '' else Decimal(default)
    except Exception:
        return Decimal(default)


@login_required
@require_permission('invoicing', 'create')
def invoice_create(request):
    company = request.company
    customers = Customer.objects.filter(company=company)
    products = Product.objects.filter(company=company, is_active=True)

    if request.method == 'POST':
        cust_id = request.POST.get('customer_id')
        customer = get_object_or_404(Customer, id=cust_id, company=company)
        
        count = Invoice.objects.filter(company=company).count() + 1
        inv_num = f"INV-{1000 + count}"
        
        is_interstate = bool(
            request.POST.get('is_interstate') in ('on', 'true', 'True', '1') or 
            (customer.state and customer.state.strip().lower() != 'kerala')
        )

        invoice = Invoice.objects.create(
            company=company,
            invoice_number=inv_num,
            customer=customer,
            invoice_type='TAX',
            date=request.POST.get('date') or timezone.now().date(),
            due_date=request.POST.get('due_date') or timezone.now().date(),
            place_of_supply=customer.state or 'Kerala',
            is_interstate=is_interstate,
            notes=request.POST.get('notes', 'Thank you for your business! Please remit payment via bank transfer.'),
            payment_terms="Payment due within 30 days.",
            bank_details="Bank: HDFC Bank | A/C: 50200012345678 | IFSC: HDFC0001234"
        )

        # Multi-item lines processing
        product_ids = request.POST.getlist('line_product_id[]') or request.POST.getlist('product_id')
        item_names = request.POST.getlist('line_item_name[]') or request.POST.getlist('item_name')
        descriptions = request.POST.getlist('line_description[]') or request.POST.getlist('description')
        hsn_codes = request.POST.getlist('line_hsn[]') or request.POST.getlist('hsn_sac_code')
        quantities = request.POST.getlist('line_qty[]') or request.POST.getlist('quantity')
        rates = request.POST.getlist('line_rate[]') or request.POST.getlist('rate')
        tax_rates = request.POST.getlist('line_tax_rate[]') or request.POST.getlist('tax_rate')

        lines_created = 0
        if descriptions:
            for idx in range(len(descriptions)):
                desc = descriptions[idx].strip()
                item_name_val = item_names[idx].strip() if idx < len(item_names) and item_names[idx] else ''
                if not desc:
                    desc = item_name_val
                if not desc:
                    continue
                qty_val = safe_decimal(quantities[idx] if idx < len(quantities) else None, default='1.00')
                rate_val = safe_decimal(rates[idx] if idx < len(rates) else None, default='0.00')
                tax_val = safe_decimal(tax_rates[idx] if idx < len(tax_rates) else None, default='0.00')
                hsn_val = hsn_codes[idx].strip() if idx < len(hsn_codes) and hsn_codes[idx] else '998314'
                prod_id = product_ids[idx] if idx < len(product_ids) and product_ids[idx] else None

                prod = None
                if prod_id and prod_id.isdigit():
                    prod = Product.objects.filter(id=int(prod_id), company=company).first()

                InvoiceLine.objects.create(
                    invoice=invoice,
                    product=prod,
                    item_name=item_name_val or (prod.name if prod else desc[:50]),
                    description=desc,
                    hsn_sac_code=hsn_val,
                    quantity=qty_val,
                    rate=rate_val,
                    tax_rate=tax_val
                )
                lines_created += 1

        if lines_created == 0:
            # Fallback single item
            desc = request.POST.get('description', 'B ONE Platform License & Software Development Services')
            rate = Decimal(request.POST.get('rate', '50000.00'))
            qty = Decimal(request.POST.get('quantity', '1.00'))
            InvoiceLine.objects.create(
                invoice=invoice,
                description=desc,
                hsn_sac_code='998314',
                quantity=qty,
                rate=rate,
                tax_rate=Decimal('18.00')
            )

        invoice.update_totals_from_lines()

        messages.success(request, f"Draft Tax Invoice {invoice.invoice_number} generated with {invoice.lines.count()} line item(s). Total: ₹{invoice.grand_total:,.2f}")
        return redirect('invoicing:invoice_detail', invoice_id=invoice.id)

    # Prepare JSON products & customers data for Alpine.js dynamic auto-fill & performant searchable dropdown
    customers_json = json.dumps([
        {
            'id': c.id,
            'name': c.name,
            'code': c.customer_code,
            'city': c.city or '',
            'state': c.state or 'Kerala',
            'gstin': c.gstin or '',
            'display': f"{c.customer_code} — {c.name} ({c.city or 'Location'}, {c.state or 'Kerala'})"
        } for c in customers
    ])

    products_json = json.dumps([
        {
            'id': p.id,
            'name': p.name,
            'sku': p.sku,
            'rate': str(p.unit_price),
            'hsn': p.hsn_sac_code,
            'tax_rate': str(p.tax_rate),
            'unit': p.unit,
            'description': p.description or p.name
        } for p in products
    ])

    return render(request, 'invoicing/invoice_form.html', {
        'customers': customers,
        'customers_json': customers_json,
        'products': products,
        'products_json': products_json,
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
        messages.success(request, f"Payment {payment.payment_number} of ₹{amount:,.2f} recorded for Invoice #{invoice.invoice_number}. Invoice status: {invoice.get_status_display()}.")

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


# ==============================================================================
# CUSTOMER PAYMENTS (ACCOUNTS RECEIVABLE)
# ==============================================================================

@login_required
@require_permission('invoicing', 'view')
def payment_list(request):
    company = request.company
    payments = CustomerPayment.objects.filter(company=company)
    total_received = sum((p.amount for p in payments.filter(status='POSTED')), Decimal('0.00'))
    return render(request, 'invoicing/payment_list.html', {
        'payments': payments,
        'total_received': total_received,
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
            messages.success(request, f"Payment {payment.payment_number} recorded & allocated ₹{alloc_amt:,.2f} to Invoice {inv.invoice_number}.")
        else:
            post_payment_to_ledger(payment, request.user)
            messages.success(request, f"Payment {payment.payment_number} recorded.")

        return redirect('invoicing:payment_list')

    customers_json = json.dumps([
        {
            'id': c.id,
            'name': c.name,
            'code': c.customer_code,
            'city': c.city or '',
            'state': c.state or 'Kerala',
            'display': f"{c.customer_code} — {c.name}"
        } for c in customers
    ])

    invoices_json = json.dumps([
        {
            'id': inv.id,
            'number': inv.invoice_number,
            'customer_id': inv.customer_id,
            'customer_name': inv.customer.name,
            'balance_due': str(inv.balance_due),
            'display': f"{inv.invoice_number} — {inv.customer.name} (Due: ₹{inv.balance_due:,.2f})"
        } for inv in invoices
    ])

    return render(request, 'invoicing/payment_form.html', {
        'customers': customers,
        'customers_json': customers_json,
        'invoices': invoices,
        'invoices_json': invoices_json,
        'active_tab': 'invoicing',
        'active_sub': 'payments'
    })


# ==============================================================================
# PRODUCTS & SERVICES CATALOG
# ==============================================================================

@login_required
@require_permission('invoicing', 'view')
def product_list(request):
    company = request.company
    products = Product.objects.filter(company=company)
    return render(request, 'invoicing/product_list.html', {
        'products': products,
        'active_tab': 'invoicing',
        'active_sub': 'products'
    })


@login_required
@require_permission('invoicing', 'create')
def product_create(request):
    company = request.company
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        sku = request.POST.get('sku', '').strip()
        item_type = request.POST.get('item_type', 'SERVICE')
        hsn = request.POST.get('hsn_sac_code', '998314').strip()
        unit = request.POST.get('unit', 'NOS').strip()
        price = Decimal(request.POST.get('unit_price', '0.00'))
        tax_rate = Decimal(request.POST.get('tax_rate', '18.00'))
        desc = request.POST.get('description', '').strip()

        prod = Product.objects.create(
            company=company,
            name=name,
            sku=sku,
            item_type=item_type,
            hsn_sac_code=hsn,
            unit=unit,
            unit_price=price,
            tax_rate=tax_rate,
            description=desc
        )
        messages.success(request, f"Product '{prod.name}' added to catalog.")
        return redirect('invoicing:product_list')

    return render(request, 'invoicing/product_form.html', {
        'active_tab': 'invoicing',
        'active_sub': 'products'
    })


@login_required
@require_permission('invoicing', 'create')
def product_quick_create(request):
    """
    AJAX endpoint called directly from invoice creation modal to register custom products on the fly.
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=400)

    try:
        data = json.loads(request.body) if request.content_type == 'application/json' else request.POST
        company = request.company
        name = data.get('name', '').strip()
        if not name:
            return JsonResponse({'success': False, 'error': 'Product name is required'}, status=400)

        item_type = data.get('item_type', 'SERVICE')
        hsn = data.get('hsn_sac_code', '998314') or '998314'
        unit = data.get('unit', 'NOS') or 'NOS'
        price = Decimal(str(data.get('unit_price', '0.00') or '0.00'))
        tax_rate = Decimal(str(data.get('tax_rate', '18.00') or '18.00'))
        desc = data.get('description', '')

        count = Product.objects.filter(company=company).count() + 1
        sku = data.get('sku') or f"PRD-{1000 + count}"

        prod = Product.objects.create(
            company=company,
            name=name,
            sku=sku,
            item_type=item_type,
            hsn_sac_code=hsn,
            unit=unit,
            unit_price=price,
            tax_rate=tax_rate,
            description=desc
        )

        return JsonResponse({
            'success': True,
            'product': {
                'id': prod.id,
                'name': prod.name,
                'sku': prod.sku,
                'rate': str(prod.unit_price),
                'hsn': prod.hsn_sac_code,
                'tax_rate': str(prod.tax_rate),
                'unit': prod.unit,
                'description': prod.description or prod.name
            }
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
@require_permission('invoicing', 'create')
def customer_quick_create(request):
    """
    AJAX endpoint called from invoice creation modal to register new customer on the fly.
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=400)

    try:
        data = json.loads(request.body) if request.content_type == 'application/json' else request.POST
        company = request.company
        name = data.get('name', '').strip()
        if not name:
            return JsonResponse({'success': False, 'error': 'Customer / Company name is required'}, status=400)

        email = data.get('email', '').strip()
        phone = data.get('phone', '').strip() or 'N/A'
        city = data.get('city', '').strip() or 'Kochi'
        state = data.get('state', '').strip() or 'Kerala'
        gstin = data.get('gstin', '').strip().upper()
        billing_address = data.get('billing_address', '').strip() or f"{city}, {state}"
        place_of_supply = data.get('place_of_supply', '').strip() or state

        count = Customer.objects.filter(company=company).count() + 1
        cust_code = f"CUST-{1000 + count}"
        while Customer.objects.filter(company=company, customer_code=cust_code).exists():
            count += 1
            cust_code = f"CUST-{1000 + count}"

        customer = Customer.objects.create(
            company=company,
            customer_code=cust_code,
            name=name,
            company_name=data.get('company_name', name),
            email=email,
            phone=phone,
            city=city,
            state=state,
            country=data.get('country', 'India') or 'India',
            pincode=data.get('pincode', ''),
            gstin=gstin,
            billing_address=billing_address,
            place_of_supply=place_of_supply
        )

        return JsonResponse({
            'success': True,
            'customer': {
                'id': customer.id,
                'name': customer.name,
                'code': customer.customer_code,
                'city': customer.city or '',
                'state': customer.state or 'Kerala',
                'gstin': customer.gstin or '',
                'display': f"{customer.customer_code} — {customer.name} ({customer.city or 'Location'}, {customer.state or 'Kerala'})"
            }
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ==============================================================================
# VENDORS (SUPPLIERS / ACCOUNTS PAYABLE)
# ==============================================================================

@login_required
@require_permission('invoicing', 'view')
def vendor_list(request):
    company = request.company
    vendors = Vendor.objects.filter(company=company)
    total_payable = sum((v.balance_due() for v in vendors), Decimal('0.00'))
    total_purchases = sum((v.total_billed() for v in vendors), Decimal('0.00'))

    return render(request, 'invoicing/vendor_list.html', {
        'vendors': vendors,
        'total_payable': total_payable,
        'total_purchases': total_purchases,
        'active_tab': 'invoicing',
        'active_sub': 'vendors'
    })


@login_required
@require_permission('invoicing', 'create')
def vendor_create(request):
    company = request.company
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        count = Vendor.objects.filter(company=company).count() + 1
        v_code = request.POST.get('vendor_code') or f"VEND-{1000 + count}"

        vendor = Vendor.objects.create(
            company=company,
            vendor_code=v_code,
            name=name,
            contact_person=request.POST.get('contact_person', ''),
            email=request.POST.get('email', ''),
            phone=request.POST.get('phone', ''),
            category=request.POST.get('category', 'Services'),
            address=request.POST.get('address', ''),
            city=request.POST.get('city', ''),
            state=request.POST.get('state', 'Kerala'),
            pincode=request.POST.get('pincode', ''),
            gstin=request.POST.get('gstin', ''),
            pan=request.POST.get('pan', ''),
            bank_name=request.POST.get('bank_name', ''),
            account_number=request.POST.get('account_number', ''),
            ifsc_code=request.POST.get('ifsc_code', ''),
            payment_terms=request.POST.get('payment_terms', 'Net 30'),
            opening_balance=Decimal(request.POST.get('opening_balance', '0.00') or '0.00'),
            notes=request.POST.get('notes', '')
        )
        messages.success(request, f"Vendor '{vendor.name}' ({vendor.vendor_code}) created.")
        return redirect('invoicing:vendor_detail', vendor_id=vendor.id)

    return render(request, 'invoicing/vendor_form.html', {
        'categories': Vendor.CATEGORY_CHOICES,
        'active_tab': 'invoicing',
        'active_sub': 'vendors'
    })


@login_required
@require_permission('invoicing', 'view')
def vendor_detail(request, vendor_id):
    company = request.company
    vendor = get_object_or_404(Vendor, id=vendor_id, company=company)
    bills = vendor.bills.all().order_by('-bill_date')
    payments = vendor.payments_made.all().order_by('-payment_date')

    return render(request, 'invoicing/vendor_detail.html', {
        'vendor': vendor,
        'bills': bills,
        'payments': payments,
        'active_tab': 'invoicing',
        'active_sub': 'vendors'
    })


# ==============================================================================
# VENDOR BILLS (PURCHASE INVOICES)
# ==============================================================================

@login_required
@require_permission('invoicing', 'view')
def bill_list(request):
    company = request.company
    bills = VendorBill.objects.filter(company=company)
    status_filter = request.GET.get('status', '')
    if status_filter:
        bills = bills.filter(status=status_filter)

    total_payable = sum((b.balance_due for b in bills.filter(status__in=['POSTED', 'PARTIALLY_PAID'])), Decimal('0.00'))
    total_billed = sum((b.grand_total for b in bills.exclude(status='CANCELLED')), Decimal('0.00'))

    return render(request, 'invoicing/bill_list.html', {
        'bills': bills,
        'status_filter': status_filter,
        'total_payable': total_payable,
        'total_billed': total_billed,
        'active_tab': 'invoicing',
        'active_sub': 'bills'
    })


@login_required
@require_permission('invoicing', 'create')
def bill_create(request):
    company = request.company
    vendors = Vendor.objects.filter(company=company, is_active=True)
    products = Product.objects.filter(company=company, is_active=True)
    expense_accounts = Account.objects.filter(company=company, account_type='EXPENSE')

    if request.method == 'POST':
        vendor_id = request.POST.get('vendor_id')
        vendor = get_object_or_404(Vendor, id=vendor_id, company=company)

        count = VendorBill.objects.filter(company=company).count() + 1
        bill_num = f"BILL-{1000 + count}"

        is_interstate = bool(
            request.POST.get('is_interstate') in ('on', 'true', 'True', '1') or 
            (vendor.state and vendor.state.strip().lower() != 'kerala')
        )
        exp_acc_id = request.POST.get('expense_account_id')
        exp_acc = Account.objects.filter(id=exp_acc_id, company=company).first() if exp_acc_id else None

        bill = VendorBill.objects.create(
            company=company,
            bill_number=bill_num,
            vendor_invoice_number=request.POST.get('vendor_invoice_number', '').strip(),
            vendor=vendor,
            bill_date=request.POST.get('bill_date') or timezone.now().date(),
            due_date=request.POST.get('due_date') or timezone.now().date(),
            place_of_supply=vendor.state or 'Kerala',
            is_interstate=is_interstate,
            expense_account=exp_acc,
            notes=request.POST.get('notes', '')
        )

        descriptions = request.POST.getlist('line_description[]') or request.POST.getlist('description')
        product_ids = request.POST.getlist('line_product_id[]') or request.POST.getlist('product_id')
        item_names = request.POST.getlist('line_item_name[]') or request.POST.getlist('item_name')
        quantities = request.POST.getlist('line_qty[]') or request.POST.getlist('quantity')
        rates = request.POST.getlist('line_rate[]') or request.POST.getlist('rate')
        tax_rates = request.POST.getlist('line_tax_rate[]') or request.POST.getlist('tax_rate')

        lines_created = 0
        if descriptions:
            for idx in range(len(descriptions)):
                desc = descriptions[idx].strip()
                if not desc:
                    continue
                qty = safe_decimal(quantities[idx] if idx < len(quantities) else None, default='1.00')
                rate = safe_decimal(rates[idx] if idx < len(rates) else None, default='0.00')
                tax_rate = safe_decimal(tax_rates[idx] if idx < len(tax_rates) else None, default='0.00')
                prod_id = product_ids[idx] if idx < len(product_ids) and product_ids[idx] else None
                item_name = item_names[idx].strip() if idx < len(item_names) and item_names[idx] else ''

                prod = Product.objects.filter(id=int(prod_id), company=company).first() if prod_id and prod_id.isdigit() else None

                VendorBillLine.objects.create(
                    bill=bill,
                    product=prod,
                    item_name=item_name or (prod.name if prod else desc[:50]),
                    description=desc,
                    hsn_sac_code=prod.hsn_sac_code if prod else '998314',
                    quantity=qty,
                    rate=rate,
                    tax_rate=tax_rate,
                    expense_account=exp_acc
                )
                lines_created += 1

        if lines_created == 0:
            desc = request.POST.get('description', 'Office / IT Procurement Expense')
            rate = Decimal(request.POST.get('rate', '10000.00'))
            qty = Decimal(request.POST.get('quantity', '1.00'))
            VendorBillLine.objects.create(
                bill=bill,
                description=desc,
                quantity=qty,
                rate=rate,
                tax_rate=Decimal('18.00'),
                expense_account=exp_acc
            )

        bill.update_totals_from_lines()

        # If user checked 'Post Immediately'
        if request.POST.get('post_immediately') == 'on':
            post_bill_to_ledger(bill, request.user)
            messages.success(request, f"Vendor Bill {bill.bill_number} created and posted to General Ledger (AP). Total: ₹{bill.grand_total:,.2f}")
        else:
            messages.success(request, f"Draft Vendor Bill {bill.bill_number} created with {bill.lines.count()} line(s). Total: ₹{bill.grand_total:,.2f}")

        return redirect('invoicing:bill_detail', bill_id=bill.id)

    vendors_json = json.dumps([
        {
            'id': v.id,
            'name': v.name,
            'code': v.vendor_code,
            'category': v.category,
            'state': v.state or 'Kerala',
            'gstin': v.gstin or '',
            'display': f"{v.vendor_code} — {v.name} ({v.category})"
        } for v in vendors
    ])

    products_json = json.dumps([
        {
            'id': p.id,
            'name': p.name,
            'sku': p.sku,
            'rate': str(p.unit_price),
            'hsn': p.hsn_sac_code,
            'tax_rate': str(p.tax_rate),
            'unit': p.unit,
            'description': p.description or p.name
        } for p in products
    ])

    return render(request, 'invoicing/bill_form.html', {
        'vendors': vendors,
        'vendors_json': vendors_json,
        'products': products,
        'products_json': products_json,
        'expense_accounts': expense_accounts,
        'active_tab': 'invoicing',
        'active_sub': 'bills'
    })


@login_required
@require_permission('invoicing', 'view')
def bill_detail(request, bill_id):
    company = request.company
    bill = get_object_or_404(VendorBill, id=bill_id, company=company)
    bank_accounts = BankAccount.objects.filter(company=company, status='ACTIVE')

    return render(request, 'invoicing/bill_detail.html', {
        'bill': bill,
        'lines': bill.lines.all(),
        'allocations': bill.allocations.all(),
        'bank_accounts': bank_accounts,
        'active_tab': 'invoicing',
        'active_sub': 'bills'
    })


@login_required
@require_permission('invoicing', 'edit')
def bill_post(request, bill_id):
    company = request.company
    bill = get_object_or_404(VendorBill, id=bill_id, company=company)

    try:
        post_bill_to_ledger(bill, request.user)
        messages.success(request, f"Vendor Bill {bill.bill_number} posted to General Ledger (Accounts Payable).")
    except Exception as e:
        messages.error(request, f"Failed to post bill: {e}")

    return redirect('invoicing:bill_detail', bill_id=bill.id)


@login_required
@require_permission('invoicing', 'create')
def record_bill_payment(request, bill_id):
    """
    Direct payment recording against a vendor bill.
    """
    company = request.company
    bill = get_object_or_404(VendorBill, id=bill_id, company=company)

    if request.method == 'POST':
        amount = Decimal(request.POST.get('amount', str(bill.balance_due)))
        method = request.POST.get('payment_method', 'BANK_TRANSFER')
        ref = request.POST.get('reference_number', '')
        bank_id = request.POST.get('bank_account_id')
        bank = BankAccount.objects.filter(id=bank_id, company=company).first() if bank_id else None

        if amount <= Decimal('0.00'):
            messages.error(request, "Disbursement amount must be greater than zero.")
            return redirect('invoicing:bill_detail', bill_id=bill.id)

        if amount > bill.balance_due:
            amount = bill.balance_due

        if bill.status == 'DRAFT':
            try:
                post_bill_to_ledger(bill, request.user)
            except Exception:
                pass

        count = PaymentMade.objects.filter(company=company).count() + 1
        pay_num = f"PMT-{1000 + count}"

        pmt = PaymentMade.objects.create(
            company=company,
            payment_number=pay_num,
            vendor=bill.vendor,
            payment_date=request.POST.get('payment_date', timezone.now().date()),
            amount=amount,
            unallocated_amount=amount,
            payment_method=method,
            paid_from_bank=bank,
            reference_number=ref,
            notes=f"Payment for Bill {bill.bill_number}"
        )

        process_and_allocate_vendor_payment(pmt, bill, amount, request.user)
        messages.success(request, f"Payment {pmt.payment_number} of ₹{amount:,.2f} recorded for Bill {bill.bill_number}. Status: {bill.get_status_display()}.")

        return redirect('invoicing:bill_detail', bill_id=bill.id)

    return redirect('invoicing:bill_detail', bill_id=bill.id)


# ==============================================================================
# PAYMENTS MADE (DISBURSEMENTS / VENDOR PAYMENTS)
# ==============================================================================

@login_required
@require_permission('invoicing', 'view')
def payment_made_list(request):
    company = request.company
    payments = PaymentMade.objects.filter(company=company)
    total_paid = sum((p.amount for p in payments.filter(status='POSTED')), Decimal('0.00'))

    return render(request, 'invoicing/payment_made_list.html', {
        'payments': payments,
        'total_paid': total_paid,
        'active_tab': 'invoicing',
        'active_sub': 'payments_made'
    })


@login_required
@require_permission('invoicing', 'create')
def payment_made_create(request):
    company = request.company
    vendors = Vendor.objects.filter(company=company, is_active=True)
    bank_accounts = BankAccount.objects.filter(company=company, status='ACTIVE')
    open_bills = VendorBill.objects.filter(company=company, status__in=['POSTED', 'PARTIALLY_PAID'])

    if request.method == 'POST':
        vendor_id = request.POST.get('vendor_id')
        vendor = get_object_or_404(Vendor, id=vendor_id, company=company)
        amount = Decimal(request.POST.get('amount', '0.00'))
        method = request.POST.get('payment_method', 'BANK_TRANSFER')
        bank_id = request.POST.get('bank_account_id')
        bank = BankAccount.objects.filter(id=bank_id, company=company).first() if bank_id else None
        ref = request.POST.get('reference_number', '')

        raw_date = request.POST.get('payment_date', '')
        payment_date = parse_date(raw_date.strip()) if isinstance(raw_date, str) and raw_date.strip() else None
        if payment_date is None:
            payment_date = timezone.now().date()

        count = PaymentMade.objects.filter(company=company).count() + 1
        pay_num = f"PMT-{1000 + count}"

        pmt = PaymentMade.objects.create(
            company=company,
            payment_number=pay_num,
            vendor=vendor,
            payment_date=payment_date,
            amount=amount,
            unallocated_amount=amount,
            payment_method=method,
            paid_from_bank=bank,
            reference_number=ref,
            notes=request.POST.get('notes', '')
        )

        bill_id = request.POST.get('bill_id')
        if bill_id:
            bill = get_object_or_404(VendorBill, id=bill_id, company=company)
            alloc_amt = min(amount, bill.balance_due)
            process_and_allocate_vendor_payment(pmt, bill, alloc_amt, request.user)
            messages.success(request, f"Disbursement {pmt.payment_number} posted and allocated ₹{alloc_amt:,.2f} to Bill {bill.bill_number}.")
        else:
            # Auto-allocate FIFO across vendor's open bills
            auto_allocate_vendor_payment_fifo(pmt, request.user)
            messages.success(request, f"Disbursement {pmt.payment_number} posted and auto-allocated across open bills.")

        return redirect('invoicing:payment_made_list')

    vendors_data = []
    for v in vendors:
        bal = v.balance_due()
        vendors_data.append({
            'id': v.id,
            'name': v.name,
            'code': v.vendor_code,
            'city': v.city or '',
            'state': v.state or 'Kerala',
            'balance_due': str(bal),
            'display': f"{v.vendor_code} — {v.name} (AP: ₹{bal:,.2f})"
        })
    vendors_json = json.dumps(vendors_data)

    open_bills_json = json.dumps([
        {
            'id': b.id,
            'number': b.bill_number,
            'vendor_id': b.vendor_id,
            'vendor_name': b.vendor.name,
            'balance_due': str(b.balance_due),
            'display': f"{b.bill_number} — {b.vendor.name} (Due: ₹{b.balance_due:,.2f})"
        } for b in open_bills
    ])

    return render(request, 'invoicing/payment_made_form.html', {
        'vendors': vendors,
        'vendors_json': vendors_json,
        'bank_accounts': bank_accounts,
        'open_bills': open_bills,
        'open_bills_json': open_bills_json,
        'active_tab': 'invoicing',
        'active_sub': 'payments_made'
    })


@login_required
@require_permission('invoicing', 'view')
def payment_made_detail(request, payment_id):
    company = request.company
    payment = get_object_or_404(PaymentMade, id=payment_id, company=company)
    return render(request, 'invoicing/payment_made_detail.html', {
        'payment': payment,
        'allocations': payment.allocations.all(),
        'active_tab': 'invoicing',
        'active_sub': 'payments_made'
    })
