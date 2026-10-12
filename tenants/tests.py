from django.test import TestCase, Client
from django.contrib.auth.models import User
from decimal import Decimal
from django.utils import timezone

from tenants.models import Company, UserProfile, CompanyMembership
from crm.models import Lead, Customer
from crm.services import convert_lead_to_customer
from invoicing.models import Invoice, InvoiceLine, CustomerPayment
from invoicing.services import process_and_allocate_payment
from accounting.models import Account, JournalEntry, JournalLine
from accounting.services import (
    ensure_default_chart_of_accounts,
    post_journal_entry,
    reverse_journal_entry,
    post_invoice_to_ledger
)
from hr.models import Employee, LeaveType, LeaveRequest
from projects.models import Project, Task, WorkLog, ProjectMessage

class BytebootERPSystemTests(TestCase):
    def setUp(self):
        # Create 2 Tenant Companies
        self.comp_a = Company.objects.create(name="Byteboot Techno Solutions", code="BYTEBOOT", gstin="32AAACB1234F1Z5")
        self.comp_b = Company.objects.create(name="Apex Digital Systems", code="APEX", gstin="33BBBCD5678F2Z6")

        # Create Users
        self.user_a = User.objects.create_user(username="admin_a", password="password123")
        self.user_b = User.objects.create_user(username="admin_b", password="password123")

        # Memberships
        CompanyMembership.objects.create(user=self.user_a, company=self.comp_a, role="COMPANY_ADMIN")
        CompanyMembership.objects.create(user=self.user_b, company=self.comp_b, role="COMPANY_ADMIN")

        # Profiles
        UserProfile.objects.create(user=self.user_a, active_company=self.comp_a)
        UserProfile.objects.create(user=self.user_b, active_company=self.comp_b)

        # Ensure Chart of Accounts
        ensure_default_chart_of_accounts(self.comp_a)
        ensure_default_chart_of_accounts(self.comp_b)

    def test_tenant_isolation(self):
        """Verify strict tenant data isolation between Company A and Company B."""
        # Create Lead in Company A
        lead_a = Lead.objects.create(
            company=self.comp_a,
            lead_number="LD-A001",
            company_name="Company A Lead",
            contact_person="Alice",
            phone="9999999999"
        )

        # Create Lead in Company B
        lead_b = Lead.objects.create(
            company=self.comp_b,
            lead_number="LD-B001",
            company_name="Company B Lead",
            contact_person="Bob",
            phone="8888888888"
        )

        # Query company A leads
        comp_a_leads = Lead.objects.filter(company=self.comp_a)
        self.assertIn(lead_a, comp_a_leads)
        self.assertNotIn(lead_b, comp_a_leads)

    def test_crm_lead_conversion(self):
        """Verify converting a WON lead to a Customer record without duplicates."""
        lead = Lead.objects.create(
            company=self.comp_a,
            lead_number="LD-100",
            company_name="Innovate Ltd",
            contact_person="Carol",
            phone="7777777777",
            email="carol@innovate.com"
        )

        customer = convert_lead_to_customer(lead, self.user_a)
        self.assertEqual(customer.company_name, "Innovate Ltd")
        self.assertEqual(lead.status, "WON")
        self.assertEqual(customer.lead_reference, lead)

    def test_double_entry_balanced_rule(self):
        """Verify balanced debit == credit requirement for general ledger journal entries."""
        je = JournalEntry.objects.create(
            company=self.comp_a,
            entry_number="JE-TEST-1",
            date=timezone.now().date(),
            description="Unbalanced test entry"
        )

        bank_acc = Account.objects.get(company=self.comp_a, code="1010")
        sales_acc = Account.objects.get(company=self.comp_a, code="4000")

        # Unbalanced line: Debit 1000, Credit 500
        JournalLine.objects.create(journal=je, account=bank_acc, debit=Decimal("1000.00"), credit=Decimal("0.00"))
        JournalLine.objects.create(journal=je, account=sales_acc, debit=Decimal("0.00"), credit=Decimal("500.00"))

        with self.assertRaises(ValueError):
            post_journal_entry(je, self.user_a)

    def test_tax_invoice_calculation_and_ledger_posting(self):
        """Verify GST invoice calculations, tax breakdown, and posting to double-entry general ledger."""
        cust = Customer.objects.create(
            company=self.comp_a,
            customer_code="CUST-T1",
            name="Test Client",
            email="client@test.com",
            phone="9000000000"
        )

        inv = Invoice.objects.create(
            company=self.comp_a,
            invoice_number="INV-T1001",
            customer=cust,
            date=timezone.now().date(),
            due_date=timezone.now().date(),
            place_of_supply="Kerala",
            is_interstate=False
        )

        InvoiceLine.objects.create(
            invoice=inv,
            description="Software Development",
            hsn_sac_code="998314",
            quantity=Decimal("1.00"),
            rate=Decimal("100000.00")
        )
        inv.update_totals_from_lines()

        # Check intra-state GST tax amounts (CGST 9% + SGST 9%)
        self.assertEqual(inv.subtotal, Decimal("100000.00"))
        self.assertEqual(inv.cgst_total, Decimal("9000.00"))
        self.assertEqual(inv.sgst_total, Decimal("9000.00"))
        self.assertEqual(inv.grand_total, Decimal("118000.00"))

        # Post to ledger
        je = post_invoice_to_ledger(inv, self.user_a)
        self.assertEqual(inv.status, "ISSUED")
        self.assertIsNotNone(inv.posted_journal)
        self.assertEqual(je.total_debit(), Decimal("118000.00"))
        self.assertEqual(je.total_credit(), Decimal("118000.00"))

    def test_customer_payment_allocation(self):
        """Verify receiving customer payment and allocating to invoice."""
        cust = Customer.objects.create(
            company=self.comp_a,
            customer_code="CUST-P1",
            name="Payee Client",
            email="payee@test.com",
            phone="9111111111"
        )

        inv = Invoice.objects.create(
            company=self.comp_a,
            invoice_number="INV-P1001",
            customer=cust,
            date=timezone.now().date(),
            due_date=timezone.now().date()
        )
        InvoiceLine.objects.create(invoice=inv, description="Services", rate=Decimal("50000.00"))
        inv.update_totals_from_lines()
        post_invoice_to_ledger(inv, self.user_a)

        payment = CustomerPayment.objects.create(
            company=self.comp_a,
            payment_number="PAY-T101",
            customer=cust,
            payment_date=timezone.now().date(),
            amount=Decimal("59000.00"),
            unallocated_amount=Decimal("59000.00")
        )

        process_and_allocate_payment(payment, inv, Decimal("59000.00"), self.user_a)
        inv.refresh_from_db()
        self.assertEqual(inv.status, "PAID")
        self.assertEqual(inv.balance_due, Decimal("0.00"))

    def test_journal_reversal_workflow(self):
        """Verify journal reversal creates an audit-traceable reversal entry."""
        je = JournalEntry.objects.create(
            company=self.comp_a,
            entry_number="JE-REV-1",
            date=timezone.now().date(),
            description="Entry to be reversed"
        )
        bank_acc = Account.objects.get(company=self.comp_a, code="1010")
        sales_acc = Account.objects.get(company=self.comp_a, code="4000")

        JournalLine.objects.create(journal=je, account=bank_acc, debit=Decimal("10000.00"))
        JournalLine.objects.create(journal=je, account=sales_acc, credit=Decimal("10000.00"))
        post_journal_entry(je, self.user_a)

        reversal_je = reverse_journal_entry(je, self.user_a, reason="Duplicate entry")
        je.refresh_from_db()

        self.assertEqual(je.status, "REVERSED")
        self.assertEqual(reversal_je.status, "POSTED")
        self.assertEqual(reversal_je.lines.first().credit, Decimal("10000.00"))

    def test_hr_leave_approval_workflow(self):
        """Verify HR leave submission and approval."""
        emp = Employee.objects.create(
            company=self.comp_a,
            employee_id="EMP-T1",
            first_name="David",
            last_name="Miller",
            email="david@test.com",
            joining_date=timezone.now().date()
        )
        lt = LeaveType.objects.create(company=self.comp_a, name="Casual Leave", yearly_quota=12)

        req = LeaveRequest.objects.create(
            company=self.comp_a,
            employee=emp,
            leave_type=lt,
            start_date=timezone.now().date(),
            end_date=timezone.now().date(),
            duration_days=Decimal("1.0"),
            reason="Family function"
        )
        self.assertEqual(req.status, "PENDING")

        req.status = "APPROVED"
        req.approver = self.user_a
        req.save()
        self.assertEqual(req.status, "APPROVED")

    def test_whatsapp_project_messaging_entity_links(self):
        """Verify Jira task creation and WhatsApp-style project messages with entity tagging."""
        prj = Project.objects.create(
            company=self.comp_a,
            code="PRJ-WM1",
            name="WhatsApp Messaging Project",
            start_date=timezone.now().date(),
            manager=self.user_a
        )
        tsk = Task.objects.create(
            company=self.comp_a,
            project=prj,
            task_number="TSK-W1",
            title="Design Chat UI Shell",
            assigned_to=self.user_a
        )

        msg = ProjectMessage.objects.create(
            project=prj,
            sender=self.user_a,
            message_text="Check task #TSK-W1 details",
            related_entity_type="TASK",
            related_entity_id="TSK-W1"
        )

        self.assertEqual(msg.related_entity_type, "TASK")
        self.assertEqual(msg.related_entity_id, "TSK-W1")

    def test_employee_user_account_and_role_creation(self):
        """Verify creating an employee with system user account and role assignment."""
        c = Client()
        c.force_login(self.user_a)

        response = c.post('/hr/employees/create/', {
            'first_name': 'Sarah',
            'last_name': 'Conner',
            'email': 'sarah@test.com',
            'phone': '9991112223',
            'department': 'Finance',
            'designation': 'Senior Accountant',
            'joining_date': '2026-10-09',
            'basic_salary': '70000.00',
            'hra': '28000.00',
            'allowance': '7000.00',
            'create_user': 'on',
            'username': 'sarah_acc',
            'password': 'Password123!',
            'role': 'ACCOUNTANT'
        })
        self.assertEqual(response.status_code, 302)

        created_user = User.objects.get(username='sarah_acc')
        self.assertEqual(created_user.email, 'sarah@test.com')
        self.assertTrue(created_user.check_password('Password123!'))

        membership = CompanyMembership.objects.get(user=created_user, company=self.comp_a)
        self.assertEqual(membership.role, 'ACCOUNTANT')

        emp = Employee.objects.get(user=created_user)
        self.assertEqual(emp.full_name, 'Sarah Conner')

