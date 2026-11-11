import json
from decimal import Decimal
from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from accounting.models import BankAccount
from accounting.services import ensure_default_chart_of_accounts
from invoicing.models import PaymentMade, Vendor, VendorBill
from tenants.models import Company, CompanyMembership, UserProfile


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
)
class PaymentMadeViewTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Test Corp", code="TESTCORP")
        self.user = User.objects.create_user(username="invoicing_admin", password="password123")
        CompanyMembership.objects.create(user=self.user, company=self.company, role="COMPANY_ADMIN")
        UserProfile.objects.create(user=self.user, active_company=self.company)
        ensure_default_chart_of_accounts(self.company)

        self.vendor = Vendor.objects.create(
            company=self.company,
            vendor_code="VEND-1001",
            name="Cloud Vendor",
            opening_balance=Decimal("500.00"),
        )
        self.bill = VendorBill.objects.create(
            company=self.company,
            bill_number="BILL-1001",
            vendor=self.vendor,
            bill_date=timezone.now().date(),
            due_date=timezone.now().date(),
            grand_total=Decimal("1500.00"),
            balance_due=Decimal("1500.00"),
            status="POSTED",
        )
        self.bank = BankAccount.objects.create(
            company=self.company,
            bank_name="HDFC Bank",
            account_name="Operating Account",
            account_number="1234567890",
            current_balance=Decimal("10000.00"),
            gl_account=self.company.accounts.filter(code="1010").first(),
        )

        self.client = Client()
        self.client.force_login(self.user)

    def test_payment_made_create_get_renders_vendor_balance(self):
        response = self.client.get("/invoicing/payments-made/create/")
        self.assertEqual(response.status_code, 200)

        vendors_data = json.loads(response.context["vendors_json"])
        self.assertEqual(len(vendors_data), 1)
        self.assertEqual(vendors_data[0]["balance_due"], "2000.00")
        self.assertEqual(
            vendors_data[0]["display"],
            "VEND-1001 — Cloud Vendor (AP: ₹2,000.00)",
        )

        bills_data = json.loads(response.context["open_bills_json"])
        self.assertEqual(len(bills_data), 1)
        self.assertEqual(bills_data[0]["balance_due"], "1500.00")

    def test_payment_made_create_post_allocates_to_bill(self):
        response = self.client.post(
            "/invoicing/payments-made/create/",
            {
                "vendor_id": str(self.vendor.id),
                "amount": "1500.00",
                "payment_date": "2026-11-11",
                "payment_method": "BANK_TRANSFER",
                "bank_account_id": str(self.bank.id),
                "bill_id": str(self.bill.id),
                "reference_number": "UTR-12345",
                "notes": "Test settlement",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.bill.refresh_from_db()
        self.assertEqual(self.bill.status, "PAID")
        self.assertEqual(self.bill.balance_due, Decimal("0.00"))
        self.assertEqual(PaymentMade.objects.filter(company=self.company).count(), 1)

    def test_payment_made_create_post_fifo_without_bill_id(self):
        response = self.client.post(
            "/invoicing/payments-made/create/",
            {
                "vendor_id": str(self.vendor.id),
                "amount": "1000.00",
                "payment_date": "",
                "payment_method": "BANK_TRANSFER",
                "bank_account_id": str(self.bank.id),
                "bill_id": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.bill.refresh_from_db()
        self.assertEqual(self.bill.status, "PARTIALLY_PAID")
        self.assertEqual(self.bill.balance_due, Decimal("500.00"))

    def test_vendor_list_renders_balance_due(self):
        response = self.client.get("/invoicing/vendors/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_payable"], Decimal("2000.00"))
