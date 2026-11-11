from django.db import models
from django.contrib.auth.models import User
from decimal import Decimal
from tenants.models import Company
from crm.models import Customer
from accounting.models import JournalEntry, Account, BankAccount, Voucher

class Product(models.Model):
    ITEM_TYPE_CHOICES = (
        ('SERVICE', 'Service'),
        ('GOODS', 'Goods / Physical Product'),
    )

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='products')
    name = models.CharField(max_length=200)
    sku = models.CharField(max_length=50, blank=True, help_text="SKU / Item Code")
    item_type = models.CharField(max_length=20, choices=ITEM_TYPE_CHOICES, default='SERVICE')
    description = models.TextField(blank=True)
    hsn_sac_code = models.CharField(max_length=20, default='998314', help_text="HSN or SAC code")
    unit = models.CharField(max_length=20, default='NOS', help_text="e.g. NOS, HRS, MONTH, PCS")
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('18.00'), help_text="GST percentage, e.g. 18.00")
    income_account = models.ForeignKey(Account, on_delete=models.SET_NULL, null=True, blank=True, related_name='product_sales_accounts')
    expense_account = models.ForeignKey(Account, on_delete=models.SET_NULL, null=True, blank=True, related_name='product_expense_accounts')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.sku or 'No SKU'}) - ₹{self.unit_price}"


class Invoice(models.Model):
    TYPE_CHOICES = (
        ('TAX', 'Tax Invoice'),
        ('PROFORMA', 'Pro Forma Invoice'),
        ('COMMERCIAL', 'Commercial Invoice'),
    )

    STATUS_CHOICES = (
        ('DRAFT', 'Draft'),
        ('ISSUED', 'Issued'),
        ('PARTIALLY_PAID', 'Partially Paid'),
        ('PAID', 'Paid'),
        ('OVERDUE', 'Overdue'),
        ('CANCELLED', 'Cancelled'),
        ('WRITTEN_OFF', 'Written Off'),
    )

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='invoices')
    invoice_number = models.CharField(max_length=50, help_text="e.g. INV-1001")
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name='invoices')
    invoice_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default='TAX')
    
    date = models.DateField()
    due_date = models.DateField()
    place_of_supply = models.CharField(max_length=100, blank=True)
    is_interstate = models.BooleanField(default=False, help_text="If True, IGST applies; else CGST+SGST")
    
    subtotal = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    cgst_total = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    sgst_total = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    igst_total = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    rounding_adjustment = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    grand_total = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    
    paid_amount = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    balance_due = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='DRAFT')
    notes = models.TextField(blank=True)
    payment_terms = models.TextField(blank=True)
    bank_details = models.TextField(blank=True)
    
    posted_journal = models.ForeignKey(JournalEntry, on_delete=models.SET_NULL, null=True, blank=True, related_name='linked_invoice')
    approved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='approved_invoices')
    
    project_id_ref = models.CharField(max_length=50, blank=True, help_text="Linked Project Code")
    quotation_id_ref = models.CharField(max_length=50, blank=True, help_text="Linked Quotation Code")
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('company', 'invoice_number')
        ordering = ['-date', '-invoice_number']

    def __str__(self):
        return f"{self.invoice_number} - {self.customer.name} ({self.status}) - ₹{self.grand_total}"

    def update_totals_from_lines(self):
        lines = self.lines.all()
        subtotal = sum((l.taxable_amount for l in lines), Decimal('0.00'))
        cgst = sum((l.cgst_amount for l in lines), Decimal('0.00'))
        sgst = sum((l.sgst_amount for l in lines), Decimal('0.00'))
        igst = sum((l.igst_amount for l in lines), Decimal('0.00'))
        
        raw_total = subtotal + cgst + sgst + igst
        # standard rounding
        rounded_total = round(raw_total)
        rounding_adj = Decimal(str(rounded_total)) - raw_total
        
        self.subtotal = subtotal
        self.cgst_total = cgst
        self.sgst_total = sgst
        self.igst_total = igst
        self.rounding_adjustment = rounding_adj
        self.grand_total = Decimal(str(rounded_total))
        self.balance_due = self.grand_total - self.paid_amount
        self.save()

    @property
    def tax_total(self):
        return self.cgst_total + self.sgst_total + self.igst_total


class InvoiceLine(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='lines')
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True, related_name='invoice_lines')
    item_name = models.CharField(max_length=255, blank=True)
    description = models.CharField(max_length=255)
    hsn_sac_code = models.CharField(max_length=20, blank=True, help_text="e.g. 998314 for IT services")
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('1.00'))
    rate = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    
    taxable_amount = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('18.00'))
    cgst_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('9.00')) # e.g. 9%
    sgst_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('9.00')) # e.g. 9%
    igst_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('18.00')) # e.g. 18%
    
    cgst_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    sgst_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    igst_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    total_amount = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))

    def calculate_line_totals(self, is_interstate=False):
        gross = self.quantity * self.rate
        self.taxable_amount = max(Decimal('0.00'), gross - self.discount_amount)
        
        if self.tax_rate is not None and self.tax_rate > Decimal('0.00'):
            self.igst_rate = self.tax_rate
            self.cgst_rate = self.tax_rate / Decimal('2.00')
            self.sgst_rate = self.tax_rate / Decimal('2.00')
        else:
            self.tax_rate = Decimal('0.00')
            self.igst_rate = Decimal('0.00')
            self.cgst_rate = Decimal('0.00')
            self.sgst_rate = Decimal('0.00')

        if is_interstate:
            self.cgst_amount = Decimal('0.00')
            self.sgst_amount = Decimal('0.00')
            if self.igst_rate > Decimal('0.00'):
                self.igst_amount = round((self.taxable_amount * self.igst_rate) / Decimal('100.00'), 2)
            else:
                self.igst_amount = Decimal('0.00')
        else:
            if self.cgst_rate > Decimal('0.00'):
                self.cgst_amount = round((self.taxable_amount * self.cgst_rate) / Decimal('100.00'), 2)
                self.sgst_amount = round((self.taxable_amount * self.sgst_rate) / Decimal('100.00'), 2)
            else:
                self.cgst_amount = Decimal('0.00')
                self.sgst_amount = Decimal('0.00')
            self.igst_amount = Decimal('0.00')
            
        self.total_amount = self.taxable_amount + self.cgst_amount + self.sgst_amount + self.igst_amount

    def save(self, *args, **kwargs):
        self.calculate_line_totals(is_interstate=self.invoice.is_interstate)
        super().save(*args, **kwargs)


class Quotation(models.Model):
    STATUS_CHOICES = (
        ('DRAFT', 'Draft'),
        ('SENT', 'Sent'),
        ('ACCEPTED', 'Accepted'),
        ('REJECTED', 'Rejected'),
        ('CONVERTED', 'Converted to Invoice'),
    )

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='quotations')
    quotation_number = models.CharField(max_length=50, help_text="e.g. QT-1001")
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name='quotations')
    date = models.DateField()
    valid_until = models.DateField()
    
    subtotal = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    tax_total = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    grand_total = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='DRAFT')
    notes = models.TextField(blank=True)
    terms = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('company', 'quotation_number')
        ordering = ['-date']

    def __str__(self):
        return f"{self.quotation_number} - {self.customer.name} (₹{self.grand_total})"


class CustomerPayment(models.Model):
    METHOD_CHOICES = (
        ('BANK_TRANSFER', 'Bank Transfer / NEFT / RTGS'),
        ('UPI', 'UPI / GPay / PhonePe'),
        ('CHEQUE', 'Cheque'),
        ('CASH', 'Cash'),
        ('CREDIT_CARD', 'Credit / Debit Card'),
    )

    STATUS_CHOICES = (
        ('DRAFT', 'Draft'),
        ('POSTED', 'Posted'),
        ('CANCELLED', 'Cancelled'),
    )

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='payments')
    payment_number = models.CharField(max_length=50, help_text="e.g. PAY-1001")
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name='payments')
    payment_date = models.DateField()
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    payment_method = models.CharField(max_length=30, choices=METHOD_CHOICES, default='BANK_TRANSFER')
    paid_into_bank = models.ForeignKey(BankAccount, on_delete=models.SET_NULL, null=True, blank=True, related_name='customer_payments')
    reference_number = models.CharField(max_length=100, blank=True, help_text="UTR / Cheque No / Txn ID")
    
    unallocated_amount = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='DRAFT')
    posted_journal = models.ForeignKey(JournalEntry, on_delete=models.SET_NULL, null=True, blank=True, related_name='linked_payment')
    voucher = models.ForeignKey(Voucher, on_delete=models.SET_NULL, null=True, blank=True, related_name='linked_customer_payments')
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('company', 'payment_number')
        ordering = ['-payment_date', '-payment_number']

    def __str__(self):
        return f"{self.payment_number} - {self.customer.name} (₹{self.amount})"


class PaymentAllocation(models.Model):
    payment = models.ForeignKey(CustomerPayment, on_delete=models.CASCADE, related_name='allocations')
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='allocations')
    allocated_amount = models.DecimalField(max_digits=15, decimal_places=2)
    allocated_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Allocated ₹{self.allocated_amount} from {self.payment.payment_number} to {self.invoice.invoice_number}"


class PaymentReceipt(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='receipts')
    receipt_number = models.CharField(max_length=50, help_text="e.g. RCT-1001")
    payment = models.OneToOneField(CustomerPayment, on_delete=models.CASCADE, related_name='receipt')
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT)
    receipt_date = models.DateField()
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('company', 'receipt_number')

    def __str__(self):
        return f"Receipt {self.receipt_number} - ₹{self.amount}"


# ==============================================================================
# ACCOUNTS PAYABLE & PURCHASING (VENDORS, BILLS, PAYMENTS MADE)
# Modeled on Ola Cars Enterprise Architecture
# ==============================================================================

class Vendor(models.Model):
    CATEGORY_CHOICES = (
        ('Services', 'Services & IT Consulting'),
        ('Cloud Tools', 'Cloud & SaaS Infrastructure'),
        ('Office Supplies', 'Office Supplies & Stationeries'),
        ('IT Equipment', 'Hardware & IT Equipment'),
        ('Legal & Audit', 'Legal, Audit & Professional'),
        ('Rent & Utilities', 'Workspace Rent & Utilities'),
        ('Marketing', 'Marketing & Advertising'),
        ('General', 'General Procurement / Other'),
    )

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='vendors')
    vendor_code = models.CharField(max_length=50, help_text="e.g. VEND-1001")
    name = models.CharField(max_length=200, help_text="Company or Vendor Trading Name")
    contact_person = models.CharField(max_length=150, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES, default='General')
    
    address = models.TextField(blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    pincode = models.CharField(max_length=20, blank=True)
    gstin = models.CharField(max_length=30, blank=True, help_text="Vendor GSTIN / Tax ID")
    pan = models.CharField(max_length=20, blank=True, help_text="PAN Number")
    
    bank_name = models.CharField(max_length=100, blank=True)
    account_number = models.CharField(max_length=50, blank=True)
    ifsc_code = models.CharField(max_length=20, blank=True)
    
    payment_terms = models.CharField(max_length=100, default='Net 30', help_text="e.g. Immediate, Net 15, Net 30")
    opening_balance = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    gl_account = models.ForeignKey(Account, on_delete=models.SET_NULL, null=True, blank=True, related_name='vendors', help_text="Accounts Payable account, default 2010")
    
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('company', 'vendor_code')
        ordering = ['name']

    def __str__(self):
        return f"{self.vendor_code} - {self.name}"

    def total_billed(self):
        return sum((b.grand_total for b in self.bills.exclude(status='CANCELLED')), Decimal('0.00'))

    def total_paid(self):
        return sum((b.paid_amount for b in self.bills.exclude(status='CANCELLED')), Decimal('0.00'))

    def balance_due(self):
        return self.total_billed() - self.total_paid() + self.opening_balance


class VendorBill(models.Model):
    STATUS_CHOICES = (
        ('DRAFT', 'Draft'),
        ('POSTED', 'Posted / Open'),
        ('PARTIALLY_PAID', 'Partially Paid'),
        ('PAID', 'Paid'),
        ('CANCELLED', 'Cancelled'),
    )

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='vendor_bills')
    bill_number = models.CharField(max_length=50, help_text="Internal bill reference e.g. BILL-1001")
    vendor_invoice_number = models.CharField(max_length=100, blank=True, help_text="Vendor's original invoice number")
    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT, related_name='bills')
    
    bill_date = models.DateField()
    due_date = models.DateField()
    place_of_supply = models.CharField(max_length=100, blank=True)
    is_interstate = models.BooleanField(default=False, help_text="If True, IGST input credit applies; else CGST+SGST")
    
    subtotal = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    cgst_total = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    sgst_total = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    igst_total = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    rounding_adjustment = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    grand_total = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    
    paid_amount = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    balance_due = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='DRAFT')
    expense_account = models.ForeignKey(Account, on_delete=models.SET_NULL, null=True, blank=True, related_name='vendor_bills', help_text="Default expense account")
    posted_journal = models.ForeignKey(JournalEntry, on_delete=models.SET_NULL, null=True, blank=True, related_name='linked_bill')
    voucher = models.ForeignKey(Voucher, on_delete=models.SET_NULL, null=True, blank=True, related_name='linked_bills')
    
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('company', 'bill_number')
        ordering = ['-bill_date', '-bill_number']

    def __str__(self):
        return f"{self.bill_number} - {self.vendor.name} (₹{self.grand_total}) [{self.status}]"

    def update_totals_from_lines(self):
        lines = self.lines.all()
        subtotal = sum((l.taxable_amount for l in lines), Decimal('0.00'))
        cgst = sum((l.cgst_amount for l in lines), Decimal('0.00'))
        sgst = sum((l.sgst_amount for l in lines), Decimal('0.00'))
        igst = sum((l.igst_amount for l in lines), Decimal('0.00'))
        
        raw_total = subtotal + cgst + sgst + igst
        rounded_total = round(raw_total)
        rounding_adj = Decimal(str(rounded_total)) - raw_total
        
        self.subtotal = subtotal
        self.cgst_total = cgst
        self.sgst_total = sgst
        self.igst_total = igst
        self.rounding_adjustment = rounding_adj
        self.grand_total = Decimal(str(rounded_total))
        self.balance_due = self.grand_total - self.paid_amount
        self.save()

    @property
    def tax_total(self):
        return self.cgst_total + self.sgst_total + self.igst_total


class VendorBillLine(models.Model):
    bill = models.ForeignKey(VendorBill, on_delete=models.CASCADE, related_name='lines')
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True, related_name='bill_lines')
    item_name = models.CharField(max_length=255, blank=True)
    description = models.CharField(max_length=255)
    hsn_sac_code = models.CharField(max_length=20, blank=True)
    expense_account = models.ForeignKey(Account, on_delete=models.SET_NULL, null=True, blank=True, related_name='bill_lines')
    
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('1.00'))
    rate = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    
    taxable_amount = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('18.00'))
    cgst_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('9.00'))
    sgst_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('9.00'))
    igst_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('18.00'))
    
    cgst_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    sgst_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    igst_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    total_amount = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))

    def calculate_line_totals(self, is_interstate=False):
        gross = self.quantity * self.rate
        self.taxable_amount = max(Decimal('0.00'), gross - self.discount_amount)
        
        if self.tax_rate is not None and self.tax_rate > Decimal('0.00'):
            self.igst_rate = self.tax_rate
            self.cgst_rate = self.tax_rate / Decimal('2.00')
            self.sgst_rate = self.tax_rate / Decimal('2.00')
        else:
            self.tax_rate = Decimal('0.00')
            self.igst_rate = Decimal('0.00')
            self.cgst_rate = Decimal('0.00')
            self.sgst_rate = Decimal('0.00')

        if is_interstate:
            self.cgst_amount = Decimal('0.00')
            self.sgst_amount = Decimal('0.00')
            if self.igst_rate > Decimal('0.00'):
                self.igst_amount = round((self.taxable_amount * self.igst_rate) / Decimal('100.00'), 2)
            else:
                self.igst_amount = Decimal('0.00')
        else:
            if self.cgst_rate > Decimal('0.00'):
                self.cgst_amount = round((self.taxable_amount * self.cgst_rate) / Decimal('100.00'), 2)
                self.sgst_amount = round((self.taxable_amount * self.sgst_rate) / Decimal('100.00'), 2)
            else:
                self.cgst_amount = Decimal('0.00')
                self.sgst_amount = Decimal('0.00')
            self.igst_amount = Decimal('0.00')
            
        self.total_amount = self.taxable_amount + self.cgst_amount + self.sgst_amount + self.igst_amount

    def save(self, *args, **kwargs):
        self.calculate_line_totals(is_interstate=self.bill.is_interstate)
        super().save(*args, **kwargs)


class PaymentMade(models.Model):
    METHOD_CHOICES = (
        ('BANK_TRANSFER', 'Bank Transfer / NEFT / RTGS'),
        ('UPI', 'UPI / NetBanking'),
        ('CHEQUE', 'Cheque'),
        ('CASH', 'Cash'),
        ('CREDIT_CARD', 'Credit / Debit Card'),
    )

    STATUS_CHOICES = (
        ('DRAFT', 'Draft'),
        ('POSTED', 'Posted'),
        ('CANCELLED', 'Cancelled'),
    )

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='payments_made')
    payment_number = models.CharField(max_length=50, help_text="e.g. PMT-1001")
    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT, related_name='payments_made')
    payment_date = models.DateField()
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    payment_method = models.CharField(max_length=30, choices=METHOD_CHOICES, default='BANK_TRANSFER')
    paid_from_bank = models.ForeignKey(BankAccount, on_delete=models.SET_NULL, null=True, blank=True, related_name='payments_made')
    reference_number = models.CharField(max_length=100, blank=True, help_text="UTR / Cheque No / Txn ID")
    
    unallocated_amount = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='DRAFT')
    posted_journal = models.ForeignKey(JournalEntry, on_delete=models.SET_NULL, null=True, blank=True, related_name='linked_payment_made')
    voucher = models.ForeignKey(Voucher, on_delete=models.SET_NULL, null=True, blank=True, related_name='linked_payments_made')
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('company', 'payment_number')
        ordering = ['-payment_date', '-payment_number']

    def __str__(self):
        return f"{self.payment_number} - {self.vendor.name} (₹{self.amount}) [{self.status}]"


class BillPaymentAllocation(models.Model):
    payment = models.ForeignKey(PaymentMade, on_delete=models.CASCADE, related_name='allocations')
    bill = models.ForeignKey(VendorBill, on_delete=models.CASCADE, related_name='allocations')
    allocated_amount = models.DecimalField(max_digits=15, decimal_places=2)
    allocated_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Allocated ₹{self.allocated_amount} from {self.payment.payment_number} to {self.bill.bill_number}"
