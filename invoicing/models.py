from django.db import models
from django.contrib.auth.models import User
from decimal import Decimal
from tenants.models import Company
from crm.models import Customer
from accounting.models import JournalEntry

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


class InvoiceLine(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='lines')
    description = models.CharField(max_length=255)
    hsn_sac_code = models.CharField(max_length=20, blank=True, help_text="e.g. 998314 for IT services")
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('1.00'))
    rate = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    
    taxable_amount = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
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
        
        if is_interstate:
            self.cgst_amount = Decimal('0.00')
            self.sgst_amount = Decimal('0.00')
            self.igst_amount = round((self.taxable_amount * self.igst_rate) / Decimal('100.00'), 2)
        else:
            self.cgst_amount = round((self.taxable_amount * self.cgst_rate) / Decimal('100.00'), 2)
            self.sgst_amount = round((self.taxable_amount * self.sgst_rate) / Decimal('100.00'), 2)
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
    reference_number = models.CharField(max_length=100, blank=True, help_text="UTR / Cheque No / Txn ID")
    
    unallocated_amount = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='DRAFT')
    posted_journal = models.ForeignKey(JournalEntry, on_delete=models.SET_NULL, null=True, blank=True, related_name='linked_payment')
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
