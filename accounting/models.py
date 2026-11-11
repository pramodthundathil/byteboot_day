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

    SUB_CATEGORY_CHOICES = (
        # Assets
        ('CASH', 'Cash in Hand'),
        ('BANK', 'Bank Account'),
        ('ACCOUNTS_RECEIVABLE', 'Accounts Receivable (Debtors)'),
        ('INVENTORY', 'Inventory / Stock'),
        ('FIXED_ASSET', 'Fixed Asset'),
        ('ACCUMULATED_DEPRECIATION', 'Accumulated Depreciation'),
        ('INPUT_TAX', 'Input Tax (GST / VAT)'),
        ('OTHER_CURRENT_ASSET', 'Other Current Asset'),
        ('OTHER_ASSET', 'Other Non-Current Asset'),
        # Liabilities
        ('ACCOUNTS_PAYABLE', 'Accounts Payable (Creditors)'),
        ('OUTPUT_TAX', 'Output Tax (GST / VAT)'),
        ('STATUTORY_DUES', 'Statutory / TDS / Payroll Dues'),
        ('OTHER_CURRENT_LIABILITY', 'Other Current Liability'),
        ('LONG_TERM_LIABILITY', 'Long-term Liability'),
        # Equity
        ('EQUITY', 'Owner Equity / Share Capital'),
        ('RETAINED_EARNINGS', 'Retained Earnings'),
        ('DRAWINGS', 'Drawings / Dividends'),
        # Income
        ('OPERATING_REVENUE', 'Operating Revenue / Sales'),
        ('OTHER_INCOME', 'Other Non-Operating Income'),
        # Expense
        ('COST_OF_SALES', 'Cost of Goods / Direct Services'),
        ('SALARY_EXPENSE', 'Salaries, Wages & Benefits'),
        ('RENT_EXPENSE', 'Rent & Workspace Expense'),
        ('OPERATING_EXPENSE', 'General & Administrative Expense'),
        ('DEPRECIATION_EXPENSE', 'Depreciation & Amortization Expense'),
        ('FINANCIAL_EXPENSE', 'Bank Charges & Gateway Fees'),
        ('OTHER_EXPENSE', 'Other Non-Operating Expense'),
    )

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='accounts')
    code = models.CharField(max_length=20, help_text="e.g. 1010, 2010")
    name = models.CharField(max_length=255)
    account_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    sub_category = models.CharField(max_length=30, choices=SUB_CATEGORY_CHOICES, default='OPERATING_EXPENSE', blank=True)
    parent = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='children')
    account_number = models.CharField(max_length=50, blank=True, help_text="Bank or external account reference")
    currency = models.CharField(max_length=10, default='INR')
    cuenta_espanol = models.CharField(max_length=255, blank=True, help_text="Spanish alias (Ola Cars bilingual feature)")
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

    def current_balance(self, as_of_date=None):
        lines = self.journal_lines.filter(journal__status='POSTED')
        if as_of_date:
            lines = lines.filter(journal__date__lte=as_of_date)
        total_debit = sum((line.debit for line in lines), Decimal('0.00'))
        total_credit = sum((line.credit for line in lines), Decimal('0.00'))
        
        # Asset / Expense balance = Opening + Debit - Credit
        if self.account_type in ('ASSET', 'EXPENSE'):
            return self.opening_balance + total_debit - total_credit
        else:
            # Liability / Equity / Income balance = Opening + Credit - Debit
            return self.opening_balance + total_credit - total_debit

    def get_debit_total(self, start_date=None, end_date=None):
        lines = self.journal_lines.filter(journal__status='POSTED')
        if start_date:
            lines = lines.filter(journal__date__gte=start_date)
        if end_date:
            lines = lines.filter(journal__date__lte=end_date)
        return sum((line.debit for line in lines), Decimal('0.00'))

    def get_credit_total(self, start_date=None, end_date=None):
        lines = self.journal_lines.filter(journal__status='POSTED')
        if start_date:
            lines = lines.filter(journal__date__gte=start_date)
        if end_date:
            lines = lines.filter(journal__date__lte=end_date)
        return sum((line.credit for line in lines), Decimal('0.00'))


class TaxRate(models.Model):
    TAX_TYPE_CHOICES = (
        ('GST', 'Goods & Services Tax (GST)'),
        ('CGST', 'Central GST (CGST)'),
        ('SGST', 'State GST (SGST)'),
        ('IGST', 'Integrated GST (IGST)'),
        ('VAT', 'Value Added Tax (VAT)'),
        ('TDS', 'Tax Deducted at Source (TDS)'),
        ('OTHER', 'Other Tax / Levy'),
    )

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='tax_rates')
    name = models.CharField(max_length=100, help_text="e.g. GST 18%, CGST 9%, SGST 9%")
    rate = models.DecimalField(max_digits=5, decimal_places=2, help_text="Percentage rate, e.g. 18.00")
    tax_type = models.CharField(max_length=20, choices=TAX_TYPE_CHOICES, default='GST')
    gl_account = models.ForeignKey(Account, on_delete=models.SET_NULL, null=True, blank=True, related_name='tax_rates')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('company', 'name')
        ordering = ['tax_type', 'rate']

    def __str__(self):
        return f"{self.name} ({self.rate}%)"


class BankAccount(models.Model):
    ACCOUNT_TYPE_CHOICES = (
        ('BANK', 'Bank Account'),
        ('CASH', 'Petty Cash / Cash Drawer'),
        ('CREDIT_CARD', 'Credit Card'),
    )

    STATUS_CHOICES = (
        ('ACTIVE', 'Active'),
        ('INACTIVE', 'Inactive'),
    )

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='bank_accounts')
    bank_name = models.CharField(max_length=150, help_text="e.g. HDFC Bank, ICICI Bank, Cash Vault")
    account_name = models.CharField(max_length=150, help_text="e.g. Main Operating Account, Petty Cash")
    account_number = models.CharField(max_length=50, help_text="Account Number or ID")
    account_type = models.CharField(max_length=20, choices=ACCOUNT_TYPE_CHOICES, default='BANK')
    currency = models.CharField(max_length=10, default='INR')
    ifsc_code = models.CharField(max_length=30, blank=True, help_text="IFSC / Routing code")
    swift_code = models.CharField(max_length=30, blank=True)
    branch_name = models.CharField(max_length=150, blank=True)
    gl_account = models.ForeignKey(Account, on_delete=models.SET_NULL, null=True, blank=True, related_name='linked_bank_accounts')
    
    opening_balance = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    current_balance = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='ACTIVE')
    description = models.TextField(blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('company', 'account_number')
        ordering = ['account_name']

    def __str__(self):
        return f"{self.account_name} ({self.bank_name} - {self.account_number}) - ₹{self.current_balance}"


class JournalEntry(models.Model):
    STATUS_CHOICES = (
        ('DRAFT', 'Draft'),
        ('POSTED', 'Posted'),
        ('REVERSED', 'Reversed'),
    )

    SOURCE_MODULE_CHOICES = (
        ('MANUAL', 'Manual Journal'),
        ('VOUCHER', 'Voucher Entry'),
        ('INVOICE', 'Customer Invoice'),
        ('PAYMENT', 'Customer Payment'),
        ('RECEIPT', 'Payment Receipt'),
        ('VENDOR_BILL', 'Vendor Bill'),
        ('VENDOR_PAYMENT', 'Vendor Payment'),
        ('EXPENSE', 'Expense Claim'),
        ('DEPRECIATION', 'Asset Depreciation'),
        ('CONTRA', 'Contra Transfer'),
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


class Voucher(models.Model):
    VOUCHER_TYPE_CHOICES = (
        ('SALES', 'Sales Voucher (SV)'),
        ('PURCHASE', 'Purchase Voucher (PV)'),
        ('RECEIPT', 'Receipt Voucher (RV)'),
        ('PAYMENT', 'Payment Voucher (PMT)'),
        ('JOURNAL', 'Journal Voucher (JV)'),
        ('CONTRA', 'Contra Voucher (CN)'),
    )

    PARTY_TYPE_CHOICES = (
        ('CUSTOMER', 'Customer'),
        ('VENDOR', 'Vendor / Supplier'),
        ('EMPLOYEE', 'Employee'),
        ('OTHER', 'Other'),
    )

    STATUS_CHOICES = (
        ('DRAFT', 'Draft'),
        ('POSTED', 'Posted'),
        ('CANCELLED', 'Cancelled'),
    )

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='vouchers')
    voucher_number = models.CharField(max_length=50, help_text="e.g. RV-20261019-0001, PMT-20261019-0001, CN-..., JV-...")
    date = models.DateField()
    voucher_type = models.CharField(max_length=20, choices=VOUCHER_TYPE_CHOICES)
    narration = models.TextField(help_text="Narration or reason for the voucher")
    reference_number = models.CharField(max_length=100, blank=True, help_text="External Ref / Cheque # / UTR")
    
    party_type = models.CharField(max_length=20, choices=PARTY_TYPE_CHOICES, default='OTHER')
    party_id = models.CharField(max_length=100, blank=True)
    party_name = models.CharField(max_length=255, blank=True)
    
    total_amount = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='DRAFT')
    auto_set_off = models.BooleanField(default=True, help_text="Auto set-off open invoices or bills")
    
    journal_entry = models.OneToOneField(JournalEntry, on_delete=models.SET_NULL, null=True, blank=True, related_name='voucher')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='created_vouchers')
    posted_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='posted_vouchers')
    posted_at = models.DateTimeField(null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('company', 'voucher_number')
        ordering = ['-date', '-voucher_number']

    def __str__(self):
        return f"{self.voucher_number} [{self.voucher_type}] - ₹{self.total_amount} ({self.status})"

    def total_debits(self):
        return sum((line.amount for line in self.lines.filter(entry_type='DEBIT')), Decimal('0.00'))

    def total_credits(self):
        return sum((line.amount for line in self.lines.filter(entry_type='CREDIT')), Decimal('0.00'))

    def is_balanced(self):
        return abs(self.total_debits() - self.total_credits()) < Decimal('0.001')


class VoucherLine(models.Model):
    ENTRY_TYPE_CHOICES = (
        ('DEBIT', 'Debit'),
        ('CREDIT', 'Credit'),
    )

    voucher = models.ForeignKey(Voucher, on_delete=models.CASCADE, related_name='lines')
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name='voucher_lines')
    entry_type = models.CharField(max_length=10, choices=ENTRY_TYPE_CHOICES)
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    description = models.CharField(max_length=255, blank=True)
    tax_rate = models.ForeignKey(TaxRate, on_delete=models.SET_NULL, null=True, blank=True, related_name='voucher_lines')
    tax_amount = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f"{self.voucher.voucher_number} line: {self.account.name} {self.entry_type} ₹{self.amount}"


class BankTransaction(models.Model):
    TXN_TYPE_CHOICES = (
        ('DEPOSIT', 'Deposit'),
        ('WITHDRAWAL', 'Withdrawal'),
        ('TRANSFER', 'Transfer'),
    )

    STATUS_CHOICES = (
        ('UNCLEARED', 'Uncleared'),
        ('CLEARED', 'Cleared'),
        ('RECONCILED', 'Reconciled'),
    )

    bank_account = models.ForeignKey(BankAccount, on_delete=models.CASCADE, related_name='transactions')
    date = models.DateField()
    transaction_type = models.CharField(max_length=20, choices=TXN_TYPE_CHOICES)
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    reference = models.CharField(max_length=100, blank=True, help_text="Txn ID / Cheque #")
    description = models.CharField(max_length=255)
    party_name = models.CharField(max_length=255, blank=True)
    voucher = models.ForeignKey(Voucher, on_delete=models.SET_NULL, null=True, blank=True, related_name='bank_transactions')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='CLEARED')
    running_balance = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-id']

    def __str__(self):
        return f"{self.bank_account.account_name} | {self.transaction_type} ₹{self.amount} ({self.date})"


class FixedAsset(models.Model):
    STATUS_CHOICES = (
        ('ACTIVE', 'Active'),
        ('DISPOSED', 'Disposed'),
        ('WRITTEN_OFF', 'Written Off'),
    )

    DEPRECIATION_METHOD_CHOICES = (
        ('STRAIGHT_LINE', 'Straight-Line Method'),
    )

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='fixed_assets')
    asset_code = models.CharField(max_length=50)
    name = models.CharField(max_length=255)
    category = models.CharField(max_length=100, help_text="e.g. IT Equipment, Furniture, Vehicles")
    location = models.CharField(max_length=100, blank=True, default="Head Office")
    serial_number = models.CharField(max_length=100, blank=True)
    
    acquisition_date = models.DateField()
    purchase_cost = models.DecimalField(max_digits=15, decimal_places=2)
    salvage_value = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    useful_life_months = models.IntegerField(default=36, help_text="Useful life in months")
    accumulated_depreciation = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    
    depreciation_method = models.CharField(max_length=30, choices=DEPRECIATION_METHOD_CHOICES, default='STRAIGHT_LINE')
    asset_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name='asset_fixed_accounts')
    accumulated_depreciation_account = models.ForeignKey(Account, on_delete=models.SET_NULL, null=True, blank=True, related_name='accum_depr_fixed_assets')
    depreciation_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name='depreciation_expense_accounts')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='ACTIVE')

    class Meta:
        unique_together = ('company', 'asset_code')
        ordering = ['name']

    def __str__(self):
        return f"{self.asset_code} - {self.name} (Cost: ₹{self.purchase_cost})"

    def book_value(self):
        return max(Decimal('0.00'), self.purchase_cost - self.accumulated_depreciation)

    def monthly_depreciation_amount(self):
        if self.useful_life_months <= 0:
            return Decimal('0.00')
        depreciable_base = max(Decimal('0.00'), self.purchase_cost - self.salvage_value)
        return round(depreciable_base / Decimal(str(self.useful_life_months)), 2)


class FixedAssetDepreciation(models.Model):
    STATUS_CHOICES = (
        ('PENDING', 'Pending'),
        ('POSTED', 'Posted'),
    )

    asset = models.ForeignKey(FixedAsset, on_delete=models.CASCADE, related_name='depreciation_entries')
    period_index = models.IntegerField(help_text="Month index 1, 2, ...")
    period_date = models.DateField()
    depreciation_amount = models.DecimalField(max_digits=15, decimal_places=2)
    accumulated_depreciation = models.DecimalField(max_digits=15, decimal_places=2)
    book_value = models.DecimalField(max_digits=15, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    journal_entry = models.ForeignKey(JournalEntry, on_delete=models.SET_NULL, null=True, blank=True, related_name='depreciation_schedules')
    posted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['period_index']

    def __str__(self):
        return f"{self.asset.asset_code} Month {self.period_index}: ₹{self.depreciation_amount} ({self.status})"


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
