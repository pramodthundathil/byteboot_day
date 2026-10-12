import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "byteboot_day.settings")
django.setup()

from decimal import Decimal
from django.contrib.auth.models import User
from django.utils import timezone
from tenants.models import Company, UserProfile, CompanyMembership
from crm.models import Lead, Customer, CustomerContact
from crm.services import convert_lead_to_customer
from accounting.services import ensure_default_chart_of_accounts, post_invoice_to_ledger, post_payment_to_ledger
from invoicing.models import Invoice, InvoiceLine, CustomerPayment
from invoicing.services import process_and_allocate_payment
from hr.models import Employee, LeaveType, LeaveRequest
from projects.models import Project, ProjectMember, Task, WorkLog, ProjectMessage

def seed():
    print("Seeding database with realistic Byteboot Business OS data...")

    # 1. Create Users
    admin_user, _ = User.objects.get_or_create(
        username="admin",
        defaults={"email": "admin@byteboot.tech", "is_staff": True, "is_superuser": True}
    )
    admin_user.set_password("admin123")
    admin_user.save()

    staff_user, _ = User.objects.get_or_create(
        username="john_developer",
        defaults={"email": "john@byteboot.tech", "first_name": "John", "last_name": "Doe"}
    )
    staff_user.set_password("staff123")
    staff_user.save()

    # 2. Create Tenants
    byteboot_comp, _ = Company.objects.get_or_create(
        code="BYTEBOOT",
        defaults={
            "name": "Byteboot Techno Solutions Pvt Ltd",
            "tagline": "Multi-Tenant Enterprise Software Architectures",
            "email": "contact@byteboot.tech",
            "phone": "+91 484 2901234",
            "address": "Infopark Campus, Kakkanad",
            "city": "Kochi",
            "state": "Kerala",
            "country": "India",
            "pincode": "682042",
            "gstin": "32AAACB1234F1Z5",
            "currency_code": "INR",
            "currency_symbol": "₹"
        }
    )

    apex_comp, _ = Company.objects.get_or_create(
        code="APEX",
        defaults={
            "name": "Apex Digital Systems Pvt Ltd",
            "tagline": "Hardware & Cloud Infrastructure Solutions",
            "email": "sales@apexdigital.in",
            "phone": "+91 44 28129999",
            "address": "Mount Road",
            "city": "Chennai",
            "state": "Tamil Nadu",
            "country": "India",
            "gstin": "33BBBCD5678F2Z6"
        }
    )

    # User Memberships
    CompanyMembership.objects.get_or_create(user=admin_user, company=byteboot_comp, defaults={"role": "COMPANY_ADMIN"})
    CompanyMembership.objects.get_or_create(user=staff_user, company=byteboot_comp, defaults={"role": "STAFF"})
    CompanyMembership.objects.get_or_create(user=admin_user, company=apex_comp, defaults={"role": "COMPANY_ADMIN"})

    # Setup UserProfile
    profile, _ = UserProfile.objects.get_or_create(user=admin_user)
    profile.active_company = byteboot_comp
    profile.save()

    # 3. Initialize Chart of Accounts
    ensure_default_chart_of_accounts(byteboot_comp)
    ensure_default_chart_of_accounts(apex_comp)

    # 4. Create Leads & Convert
    lead1, _ = Lead.objects.get_or_create(
        company=byteboot_comp,
        lead_number="LD-10001",
        defaults={
            "company_name": "Policybazar Investments",
            "contact_person": "Pramod Gopinath",
            "phone": "+91 9526377110",
            "email": "pramod@policybazar.com",
            "address": "MG Road, Kochi",
            "service_interested": "Enterprise Business OS ERP & Mobile App",
            "estimated_value": Decimal("450000.00"),
            "status": "QUALIFIED",
            "created_by": admin_user,
            "assigned_to": admin_user
        }
    )

    cust1 = convert_lead_to_customer(lead1, admin_user)

    # 5. Create Invoices & Post to Ledger
    inv1, created = Invoice.objects.get_or_create(
        company=byteboot_comp,
        invoice_number="INV-1001",
        defaults={
            "customer": cust1,
            "invoice_type": "TAX",
            "date": timezone.now().date(),
            "due_date": timezone.now().date(),
            "place_of_supply": "Kerala",
            "is_interstate": False,
            "notes": "Thank you for choosing Byteboot Techno Solutions."
        }
    )
    if created:
        InvoiceLine.objects.create(
            invoice=inv1,
            description="Byteboot Business OS Core SaaS Platform License & Deployment",
            hsn_sac_code="998314",
            quantity=Decimal("1.00"),
            rate=Decimal("200000.00")
        )
        inv1.update_totals_from_lines()
        post_invoice_to_ledger(inv1, admin_user)

    # 6. Record Payment & Allocation
    pay1, created_pay = CustomerPayment.objects.get_or_create(
        company=byteboot_comp,
        payment_number="PAY-1001",
        defaults={
            "customer": cust1,
            "payment_date": timezone.now().date(),
            "amount": Decimal("236000.00"),
            "unallocated_amount": Decimal("236000.00"),
            "payment_method": "BANK_TRANSFER",
            "reference_number": "UTR9988776655"
        }
    )
    if created_pay:
        process_and_allocate_payment(pay1, inv1, Decimal("236000.00"), admin_user)

    # 7. HR Employees & Leaves
    emp1, _ = Employee.objects.get_or_create(
        company=byteboot_comp,
        employee_id="EMP-101",
        defaults={
            "user": staff_user,
            "first_name": "John",
            "last_name": "Doe",
            "email": "john@byteboot.tech",
            "phone": "+91 9876543210",
            "department": "Engineering",
            "designation": "Senior Full Stack Architect",
            "joining_date": timezone.now().date(),
            "basic_salary": Decimal("60000.00"),
            "hra": Decimal("24000.00"),
            "allowance": Decimal("6000.00")
        }
    )

    lt_casual = LeaveType.objects.filter(company=byteboot_comp, name="Casual Leave").first()
    if lt_casual:
        LeaveRequest.objects.get_or_create(
            company=byteboot_comp,
            employee=emp1,
            start_date=timezone.now().date(),
            end_date=timezone.now().date(),
            defaults={
                "leave_type": lt_casual,
                "duration_days": Decimal("1.0"),
                "reason": "Personal work at Kochi bank branch",
                "status": "APPROVED",
                "approver": admin_user
            }
        )

    # 8. Projects, Tasks, and WhatsApp-Style Project Communication
    prj1, _ = Project.objects.get_or_create(
        company=byteboot_comp,
        code="PRJ-101",
        defaults={
            "name": "Byteboot Business OS - Multi-Tenant ERP",
            "customer": cust1,
            "description": "Enterprise Business OS for Byteboot Techno Solutions Pvt Ltd with double-entry accounting, CRM, HR, invoicing and team chat.",
            "start_date": timezone.now().date(),
            "manager": admin_user,
            "budget": Decimal("500000.00"),
            "estimated_hours": Decimal("300.00")
        }
    )

    ProjectMember.objects.get_or_create(project=prj1, user=admin_user, defaults={"role": "MANAGER"})
    ProjectMember.objects.get_or_create(project=prj1, user=staff_user, defaults={"role": "DEVELOPER"})

    tsk1, _ = Task.objects.get_or_create(
        company=byteboot_comp,
        task_number="TSK-501",
        defaults={
            "project": prj1,
            "title": "Double-Entry Accounting Engine & GST Tax Calculations",
            "description": "Implement balanced debit/credit posting, CGST/SGST/IGST tax engine, and financial reports.",
            "assigned_to": staff_user,
            "reporter": admin_user,
            "status": "DONE",
            "priority": "HIGH",
            "estimated_hours": Decimal("16.00")
        }
    )

    WorkLog.objects.get_or_create(
        company=byteboot_comp,
        task=tsk1,
        user=staff_user,
        work_date=timezone.now().date(),
        defaults={"hours_spent": Decimal("8.00"), "description": "Completed accounting engine and verified debit/credit balance tests."}
    )

    # WhatsApp-style Project Messages with Entity Links
    ProjectMessage.objects.get_or_create(
        project=prj1,
        message_text="Hi team! The Tax Invoice INV-1001 for Policybazar Investments has been generated and posted to the General Ledger.",
        related_entity_type="INVOICE",
        related_entity_id="INV-1001",
        defaults={"sender": admin_user}
    )

    ProjectMessage.objects.get_or_create(
        project=prj1,
        message_text="Great! Task TSK-501 for double-entry accounting engine is completed and fully verified with unit tests.",
        related_entity_type="TASK",
        related_entity_id="TSK-501",
        defaults={"sender": staff_user}
    )

    print("Successfully seeded Byteboot Business OS database!")

if __name__ == "__main__":
    seed()
