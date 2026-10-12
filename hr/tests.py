from django.test import TestCase, Client
from django.contrib.auth.models import User
from decimal import Decimal
from tenants.models import Company, CompanyMembership
from hr.models import Employee, LeaveType, LeaveRequest

class LeaveApplicationLifecycleTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.company = Company.objects.create(name="ByteBoot Inc", code="BYTE")
        
        # Admin user
        self.admin_user = User.objects.create_superuser('adminuser', 'admin@example.com', 'pass123')
        self.admin_mem = CompanyMembership.objects.create(
            user=self.admin_user, company=self.company, role='COMPANY_ADMIN'
        )

        # Staff user
        self.staff_user = User.objects.create_user('john_staff', 'john@example.com', 'pass123')
        self.staff_mem = CompanyMembership.objects.create(
            user=self.staff_user, company=self.company, role='STAFF'
        )
        self.employee = Employee.objects.create(
            company=self.company,
            employee_id="EMP-101",
            user=self.staff_user,
            first_name="John",
            last_name="Doe",
            email="john@example.com",
            joining_date="2026-01-01"
        )

        self.leave_type = LeaveType.objects.create(
            company=self.company, name="Casual Leave", yearly_quota=12
        )

    def test_leave_application_lifecycle(self):
        # 1. Staff user submits leave application
        self.client.login(username='john_staff', password='pass123')
        session = self.client.session
        session['active_company_id'] = self.company.id
        session.save()

        response = self.client.post('/hr/leaves/request/', {
            'employee_id': self.employee.id,
            'leave_type_id': self.leave_type.id,
            'start_date': '2026-11-01',
            'end_date': '2026-11-03',
            'duration_days': '3.0',
            'reason': 'Vacation'
        })
        self.assertEqual(response.status_code, 302)

        leave = LeaveRequest.objects.get(employee=self.employee)
        self.assertEqual(leave.status, 'PENDING')
        self.assertEqual(leave.reason, 'Vacation')

        # 2. Staff edits pending leave application
        response = self.client.post(f'/hr/leaves/{leave.id}/edit/', {
            'leave_type_id': self.leave_type.id,
            'start_date': '2026-11-01',
            'end_date': '2026-11-05',
            'duration_days': '5.0',
            'reason': 'Extended Vacation'
        })
        self.assertEqual(response.status_code, 302)
        leave.refresh_from_db()
        self.assertEqual(leave.duration_days, Decimal('5.0'))
        self.assertEqual(leave.reason, 'Extended Vacation')

        # 3. Admin approves leave request
        self.client.login(username='adminuser', password='pass123')
        session = self.client.session
        session['active_company_id'] = self.company.id
        session.save()

        response = self.client.get(f'/hr/leaves/{leave.id}/approve/')
        self.assertEqual(response.status_code, 302)
        leave.refresh_from_db()
        self.assertEqual(leave.status, 'APPROVED')

        # 4. Admin reverts approved leave back to PENDING
        response = self.client.get(f'/hr/leaves/{leave.id}/revert/')
        self.assertEqual(response.status_code, 302)
        leave.refresh_from_db()
        self.assertEqual(leave.status, 'PENDING')

        # 5. Staff cancels and deletes leave application
        self.client.login(username='john_staff', password='pass123')
        session = self.client.session
        session['active_company_id'] = self.company.id
        session.save()

        response = self.client.get(f'/hr/leaves/{leave.id}/cancel/')
        self.assertEqual(response.status_code, 302)
        leave.refresh_from_db()
        self.assertEqual(leave.status, 'CANCELLED')

        response = self.client.get(f'/hr/leaves/{leave.id}/delete/')
        self.assertEqual(response.status_code, 302)
        self.assertFalse(LeaveRequest.objects.filter(id=leave.id).exists())
