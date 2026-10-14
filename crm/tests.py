from datetime import datetime, date, time, timedelta
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.utils import timezone
from django.urls import reverse
from tenants.models import Company, CompanyMembership, UserProfile
from crm.models import Lead, LeadFollowup


class CRMCallHistoryTests(TestCase):
    def setUp(self):
        self.client = Client()

        # Create Company
        self.company = Company.objects.create(
            name="Apex Technologies",
            code="APEX",
            is_active=True
        )

        # Create Admin User
        self.admin_user = User.objects.create_user(
            username="admin_user",
            email="admin@apex.com",
            password="password123",
            first_name="Admin",
            last_name="Manager"
        )
        UserProfile.objects.create(user=self.admin_user, active_company=self.company)
        self.admin_membership = CompanyMembership.objects.create(
            user=self.admin_user,
            company=self.company,
            role="COMPANY_ADMIN",
            is_active=True
        )

        # Create Staff 1 User
        self.staff1 = User.objects.create_user(
            username="staff_sarah",
            email="sarah@apex.com",
            password="password123",
            first_name="Sarah",
            last_name="Connor"
        )
        UserProfile.objects.create(user=self.staff1, active_company=self.company)
        self.staff1_membership = CompanyMembership.objects.create(
            user=self.staff1,
            company=self.company,
            role="STAFF",
            is_active=True,
            permissions={'crm': {'view': True, 'edit': True}}
        )

        # Create Staff 2 User
        self.staff2 = User.objects.create_user(
            username="staff_john",
            email="john@apex.com",
            password="password123",
            first_name="John",
            last_name="Doe"
        )
        UserProfile.objects.create(user=self.staff2, active_company=self.company)
        self.staff2_membership = CompanyMembership.objects.create(
            user=self.staff2,
            company=self.company,
            role="STAFF",
            is_active=True,
            permissions={'crm': {'view': True, 'edit': True}}
        )

        now = timezone.now()
        today = timezone.localtime(now).date()
        tomorrow = today + timedelta(days=1)
        yesterday = today - timedelta(days=1)

        # Create Leads for Staff 1
        self.lead_s1 = Lead.objects.create(
            company=self.company,
            lead_number="LD-1001",
            company_name="Acme Corp",
            contact_person="Alice Smith",
            phone="+91 9876543210",
            assigned_to=self.staff1,
            created_by=self.staff1,
            status="CONTACTED",
            next_followup_date=timezone.make_aware(datetime.combine(tomorrow, time(11, 0)))
        )

        # Create Leads for Staff 2
        self.lead_s2 = Lead.objects.create(
            company=self.company,
            lead_number="LD-1002",
            company_name="Wayne Enterprises",
            contact_person="Bruce Wayne",
            phone="+91 9876543211",
            assigned_to=self.staff2,
            created_by=self.staff2,
            status="NEW",
            next_followup_date=timezone.make_aware(datetime.combine(today, time(14, 0)))
        )

        # Followup by Staff 1 today
        self.fup_today = LeadFollowup.objects.create(
            lead=self.lead_s1,
            staff=self.staff1,
            method="PHONE",
            summary="Called Alice, discussed ERP cloud deployment.",
            outcome="Interested",
            next_action="Send quotation",
            next_followup_date=timezone.make_aware(datetime.combine(tomorrow, time(11, 0)))
        )

        # Followup by Staff 2 yesterday
        self.fup_yesterday = LeadFollowup.objects.create(
            lead=self.lead_s2,
            staff=self.staff2,
            method="WHATSAPP",
            summary="Shared brochure over WhatsApp.",
            outcome="Pending response"
        )
        LeadFollowup.objects.filter(id=self.fup_yesterday.id).update(
            interaction_time=timezone.make_aware(datetime.combine(yesterday, time(15, 30)))
        )

    def test_admin_can_view_all_staff_calls(self):
        """Admin should see company-wide calls and all staff reports"""
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse('crm:call_history') + '?tab=all')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['is_admin'])
        # Total calls count should include both followups
        self.assertEqual(response.context['total_calls_count'], 2)
        # Staff reports summary should be populated for admins
        self.assertGreaterEqual(len(response.context['staff_call_reports']), 2)

    def test_staff_restricted_to_assigned_numbers_and_calls(self):
        """Regular staff should ONLY see their respective assigned numbers and call history"""
        self.client.force_login(self.staff1)
        response = self.client.get(reverse('crm:call_history') + '?tab=all')

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['is_admin'])
        # Staff 1 should only see their own call
        self.assertEqual(response.context['total_calls_count'], 1)
        # Staff call reports summary should NOT be visible to regular staff
        self.assertEqual(len(response.context['staff_call_reports']), 0)
        # Assigned count for Staff 1 should be 1
        self.assertEqual(response.context['assigned_leads_count'], 1)

    def test_calls_done_today_and_tomorrow_counts(self):
        """Verify proper counts for calls done today and calls scheduled for tomorrow"""
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse('crm:call_history') + '?tab=today')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['today_calls_count'], 1)
        # Tomorrow scheduled call count
        self.assertEqual(response.context['tomorrow_calls_count'], 1)
        # Yesterday calls count
        self.assertEqual(response.context['yesterday_calls_count'], 1)

    def test_date_sorting_and_pagination(self):
        """Verify date sorting and backend pagination parameters"""
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse('crm:call_history') + '?tab=all&sort=interaction_time&page_size=10&page=1')

        self.assertEqual(response.status_code, 200)
        page = response.context['records_page']
        self.assertEqual(page.number, 1)
        self.assertEqual(page.paginator.per_page, 10)
        self.assertEqual(page.paginator.count, 2)

    def test_export_call_history_excel(self):
        """Verify call history export generates Excel response"""
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse('crm:call_history_export') + '?tab=all')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response['Content-Type'],
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        self.assertTrue('crm_call_report_' in response['Content-Disposition'])

    def test_quick_followup_next_redirect(self):
        """Verify quick followup redirects back to next parameter if specified"""
        self.client.force_login(self.staff1)
        target_next = reverse('crm:call_history') + '?tab=today'
        response = self.client.post(
            reverse('crm:lead_quick_followup', kwargs={'lead_id': self.lead_s1.id}),
            {
                'summary': 'Followup via phone call completed.',
                'method': 'PHONE',
                'outcome': 'Connected',
                'status': 'CONTACTED',
                'next': target_next
            }
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, target_next)
