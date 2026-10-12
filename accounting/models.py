from django.db import models
from django.contrib.auth.models import User
from decimal import Decimal
from tenants.models import Company

class Account(models.Model):
    TYPE_CHOICES = (
        ('ASSET', 'Asset'),
        ('LIABILITY', 'Liability'),
        ('EQUITY', 'Equity'),
        ('INCOME', 'Income'),
        ('EXPENSE', 'Expense'),
    )

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='accounts')
    code = models.CharField(max_length=20, help_text="e.g. 1010, 2010")
    name = models.CharField(max_length=255)
    account_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    parent = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='children')
    description = models.TextField(blank=True)
    
    opening_balance = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    is_system = models.BooleanField(default=False, help_text="System default accounts cannot be deleted")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('company', 'code')
        ordering = ['code']

    def __str__(self):
        return f"{self.code} - {self.name} ({self.account_type})"

    def current_balance(self):
        lines = self.journal_lines.filter(journal__status='POSTED')
        total_debit = sum((line.debit for line in lines), Decimal('0.00'))
        total_credit = sum((line.credit for line in lines), Decimal('0.00'))
        
        # Asset / Expense balance = Opening + Debit - Credit
        if self.account_type in ('ASSET', 'EXPENSE'):
            return self.opening_balance + total_debit - total_credit
        else:
            # Liability / Equity / Income balance = Opening + Credit - Debit
            return self.opening_balance + total_credit - total_debit


class JournalEntry(models.Model):
    STATUS_CHOICES = (
        ('DRAFT', 'Draft'),
        ('POSTED', 'Posted'),
        ('REVERSED', 'Reversed'),
    )

    SOURCE_MODULE_CHOICES = (
        ('MANUAL', 'Manual Journal'),
        ('INVOICE', 'Customer Invoice'),
        ('PAYMENT', 'Customer Payment'),
        ('RECEIPT', 'Payment Receipt'),
        ('VENDOR_BILL', 'Vendor Bill'),
        ('VENDOR_PAYMENT', 'Vendor Payment'),
        ('EXPENSE', 'Expense Claim'),
        ('DEPRECIATION', 'Asset Depreciation'),
        ('WRITE_OFF', 'Bad Debt Write-off'),
        ('PAYROLL', 'Payroll Entry'),
    )

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='journal_entries')
    entry_number = models.CharField(max_length=50, help_text="e.g. JE-10001")
    date = models.DateField()
    reference = models.CharField(max_length=100, blank=True)
    description = models.TextField()
    
    source_module = models.CharField(max_length=30, choices=SOURCE_MODULE_CHOICES, default='MANUAL')
    source_id = models.CharField(max_length=100, blank=True)
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='DRAFT')
    posted_at = models.DateTimeField(null=True, blank=True)
    posted_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='posted_journals')
    
    reversal_of = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='reversed_by')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('company', 'entry_number')
        ordering = ['-date', '-entry_number']

    def __str__(self):
        return f"{self.entry_number} [{self.status}] - {self.description[:40]}"

    def total_debit(self):
        return sum((line.debit for line in self.lines.all()), Decimal('0.00'))

    def total_credit(self):
        return sum((line.credit for line in self.lines.all()), Decimal('0.00'))

    def is_balanced(self):
        return abs(self.total_debit() - self.total_credit()) < Decimal('0.001')


class JournalLine(models.Model):
    journal = models.ForeignKey(JournalEntry, on_delete=models.CASCADE, related_name='lines')
    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name='journal_lines')
    description = models.CharField(max_length=255, blank=True)
    debit = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    credit = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    
    party_type = models.CharField(max_length=50, blank=True, help_text="e.g. Customer, Vendor, Employee")
    party_id = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f"Line #{self.id} for {self.journal.entry_number}: {self.account.name} DR:{self.debit} CR:{self.credit}"


class FixedAsset(models.Model):
    STATUS_CHOICES = (
        ('ACTIVE', 'Active'),
        ('DISPOSED', 'Disposed'),
        ('WRITTEN_OFF', 'Written Off'),
    )

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='fixed_assets')
    asset_code = models.CharField(max_length=50)
    name = models.CharField(max_length=255)
    category = models.CharField(max_length=100, help_text="e.g. IT Equipment, Furniture, Vehicle")
    acquisition_date = models.DateField()
    purchase_cost = models.DecimalField(max_digits=15, decimal_places=2)
    salvage_value = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    useful_life_months = models.IntegerField(default=36)
    accumulated_depreciation = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    asset_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name='asset_fixed_accounts')
    depreciation_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name='depreciation_expense_accounts')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='ACTIVE')

    class Meta:
        unique_together = ('company', 'asset_code')
        ordering = ['name']

    def __str__(self):
        return f"{self.asset_code} - {self.name}"

    def book_value(self):
        return self.purchase_cost - self.accumulated_depreciation


class AccountingPeriod(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='accounting_periods')
    financial_year = models.CharField(max_length=50, help_text="e.g. 2025-2026")
    start_date = models.DateField()
    end_date = models.DateField()
    is_closed = models.BooleanField(default=False)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        unique_together = ('company', 'financial_year')
        ordering = ['-start_date']

    def __str__(self):
        return f"{self.company.code} - FY {self.financial_year} ({'Closed' if self.is_closed else 'Open'})"
