from django.test import TestCase, Client
from django.contrib.auth.models import User
from tenants.models import Company, CompanyMembership
from projects.models import Project, Task

class TaskVisibilityTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.company = Company.objects.create(name="ByteBoot Inc", code="BYTE")

        # Admin user
        self.admin = User.objects.create_superuser('admin_user', 'admin@example.com', 'pass123')
        CompanyMembership.objects.create(user=self.admin, company=self.company, role='COMPANY_ADMIN')

        # Employee 1
        self.user1 = User.objects.create_user('emp1', 'emp1@example.com', 'pass123')
        CompanyMembership.objects.create(user=self.user1, company=self.company, role='STAFF')

        # Employee 2
        self.user2 = User.objects.create_user('emp2', 'emp2@example.com', 'pass123')
        CompanyMembership.objects.create(user=self.user2, company=self.company, role='STAFF')

        # Project
        self.project = Project.objects.create(
            company=self.company, code="PRJ-101", name="Alpha Project", start_date="2026-01-01"
        )

        # Task assigned specifically to User 1
        self.task_user1 = Task.objects.create(
            company=self.company, project=self.project, task_number="TSK-501",
            title="User 1 Task", assigned_to=self.user1, reporter=self.admin
        )
        self.task_user1.assignees.set([self.user1])

        # Task assigned specifically to User 2
        self.task_user2 = Task.objects.create(
            company=self.company, project=self.project, task_number="TSK-502",
            title="User 2 Task", assigned_to=self.user2, reporter=self.admin
        )
        self.task_user2.assignees.set([self.user2])

        # Task assigned to ALL employees
        self.task_all = Task.objects.create(
            company=self.company, project=self.project, task_number="TSK-503",
            title="All Employees Task", assigned_to=self.user1, reporter=self.admin
        )
        self.task_all.assignees.set([self.admin, self.user1, self.user2])

    def test_admin_sees_all_tasks(self):
        self.client.login(username='admin_user', password='pass123')
        session = self.client.session
        session['active_company_id'] = self.company.id
        session.save()

        response = self.client.get('/projects/tasks/')
        self.assertEqual(response.status_code, 200)
        tasks = response.context['tasks']
        self.assertEqual(len(tasks), 3)

    def test_employee_only_sees_assigned_and_all_tasks(self):
        self.client.login(username='emp1', password='pass123')
        session = self.client.session
        session['active_company_id'] = self.company.id
        session.save()

        response = self.client.get('/projects/tasks/')
        self.assertEqual(response.status_code, 200)
        tasks = list(response.context['tasks'])
        
        # User 1 should see task_user1 and task_all, but NOT task_user2
        task_numbers = [t.task_number for t in tasks]
        self.assertIn("TSK-501", task_numbers)
        self.assertIn("TSK-503", task_numbers)
        self.assertNotIn("TSK-502", task_numbers)

    def test_project_visibility_and_access_restriction(self):
        # Create Project Beta assigned only to User 2
        project_beta = Project.objects.create(
            company=self.company, code="PRJ-102", name="Beta Project", start_date="2026-01-01", manager=self.admin
        )
        # Assign User 1 to Alpha Project, User 2 to Beta Project
        from projects.models import ProjectMember
        ProjectMember.objects.create(project=self.project, user=self.user1, role='DEVELOPER')
        ProjectMember.objects.create(project=project_beta, user=self.user2, role='DEVELOPER')

        # 1. User 1 visits project dashboard / list: should only see Project Alpha
        self.client.login(username='emp1', password='pass123')
        session = self.client.session
        session['active_company_id'] = self.company.id
        session.save()

        res = self.client.get('/projects/projects/')
        self.assertEqual(res.status_code, 200)
        p_codes = [p.code for p in res.context['projects']]
        self.assertIn("PRJ-101", p_codes)
        self.assertNotIn("PRJ-102", p_codes)

        # User 1 accesses detail of Project Alpha -> Allowed
        res_detail_alpha = self.client.get(f'/projects/projects/{self.project.id}/')
        self.assertEqual(res_detail_alpha.status_code, 200)

        # User 1 accesses detail of Project Beta -> Denied / Redirected
        res_detail_beta = self.client.get(f'/projects/projects/{project_beta.id}/')
        self.assertEqual(res_detail_beta.status_code, 302)
        self.assertRedirects(res_detail_beta, '/projects/projects/')

        # 2. User 2 visits project dashboard / list: should only see Project Beta
        self.client.login(username='emp2', password='pass123')
        session = self.client.session
        session['active_company_id'] = self.company.id
        session.save()

        res2 = self.client.get('/projects/projects/')
        self.assertEqual(res2.status_code, 200)
        p_codes2 = [p.code for p in res2.context['projects']]
        self.assertIn("PRJ-102", p_codes2)
        self.assertNotIn("PRJ-101", p_codes2)

        # 3. Admin visits project dashboard: sees both
        self.client.login(username='admin_user', password='pass123')
        session = self.client.session
        session['active_company_id'] = self.company.id
        session.save()

        res_admin = self.client.get('/projects/projects/')
        self.assertEqual(res_admin.status_code, 200)
        p_codes_admin = [p.code for p in res_admin.context['projects']]
        self.assertIn("PRJ-101", p_codes_admin)
        self.assertIn("PRJ-102", p_codes_admin)

    def test_project_edit_and_assign_members(self):
        from projects.models import ProjectMember
        self.client.login(username='admin_user', password='pass123')
        session = self.client.session
        session['active_company_id'] = self.company.id
        session.save()

        # GET project_edit page
        get_res = self.client.get(f'/projects/projects/{self.project.id}/edit/')
        self.assertEqual(get_res.status_code, 200)

        # POST updates to project: update name, budget, and assign emp1 as DEVELOPER and emp2 as QA
        post_data = {
            'name': 'Alpha Project Updated',
            'start_date': '2026-01-01',
            'manager_id': self.admin.id,
            'status': 'IN_PROGRESS',
            'priority': 'HIGH',
            'budget': '150000.00',
            'estimated_hours': '120.00',
            'description': 'Updated scope description',
            'member_ids': [str(self.admin.id), str(self.user1.id), str(self.user2.id)],
            f'role_{self.admin.id}': 'MANAGER',
            f'role_{self.user1.id}': 'DEVELOPER',
            f'role_{self.user2.id}': 'QA',
        }
        post_res = self.client.post(f'/projects/projects/{self.project.id}/edit/', post_data)
        self.assertRedirects(post_res, f'/projects/projects/{self.project.id}/')

        # Check DB updates
        self.project.refresh_from_db()
        self.assertEqual(self.project.name, 'Alpha Project Updated')
        self.assertEqual(self.project.priority, 'HIGH')

        members = ProjectMember.objects.filter(project=self.project)
        self.assertEqual(members.count(), 3)
        user1_member = members.get(user=self.user1)
        self.assertEqual(user1_member.role, 'DEVELOPER')
        user2_member = members.get(user=self.user2)
        self.assertEqual(user2_member.role, 'QA')

    def test_conversations_visibility_and_access_restriction(self):
        project_beta = Project.objects.create(
            company=self.company, code="PRJ-102", name="Beta Project", start_date="2026-01-01", manager=self.admin
        )
        from projects.models import ProjectMember
        ProjectMember.objects.create(project=self.project, user=self.user1, role='DEVELOPER')
        ProjectMember.objects.create(project=project_beta, user=self.user2, role='DEVELOPER')

        # User 1 visits conversations: only PRJ-101 in project list
        self.client.login(username='emp1', password='pass123')
        session = self.client.session
        session['active_company_id'] = self.company.id
        session.save()

        res = self.client.get('/projects/conversations/')
        self.assertEqual(res.status_code, 200)
        p_codes = [p.code for p in res.context['projects']]
        self.assertIn("PRJ-101", p_codes)
        self.assertNotIn("PRJ-102", p_codes)

        # User 1 tries to access PRJ-102 conversation directly via ?project_id=
        res_unauthorized = self.client.get(f'/projects/conversations/?project_id={project_beta.id}')
        self.assertEqual(res_unauthorized.status_code, 200)
        # Should not have active_project as beta
        self.assertEqual(res_unauthorized.context['active_project'].id, self.project.id)

    def test_task_create_and_edit_with_datetime_and_estimated_hours(self):
        self.client.login(username='admin_user', password='pass123')
        session = self.client.session
        session['active_company_id'] = self.company.id
        session.save()

        # 1. Create a task with start_date, due_date (datetime) and estimated_hours
        create_data = {
            'project_id': self.project.id,
            'title': 'Test Schedule Task',
            'status': 'TO_DO',
            'priority': 'HIGH',
            'estimated_hours': '6.5',
            'start_date': '2026-10-17T09:00',
            'due_date': '2026-10-17T15:30',
            'assigned_to_id': self.user1.id,
            'description': 'Testing start and due datetime calculations'
        }
        res_create = self.client.post('/projects/tasks/create/', create_data)
        self.assertEqual(res_create.status_code, 302)

        task = Task.objects.get(title='Test Schedule Task')
        self.assertEqual(float(task.estimated_hours), 6.5)
        self.assertIsNotNone(task.start_date)
        self.assertIsNotNone(task.due_date)
        from django.utils import timezone
        local_start = timezone.localtime(task.start_date)
        local_due = timezone.localtime(task.due_date)
        self.assertEqual(local_start.year, 2026)
        self.assertEqual(local_start.month, 10)
        self.assertEqual(local_start.day, 17)
        self.assertEqual(local_start.hour, 9)
        self.assertEqual(local_start.minute, 0)
        self.assertEqual(local_due.hour, 15)
        self.assertEqual(local_due.minute, 30)

        # 2. Edit the task with updated estimated_hours and due_date
        edit_data = {
            'project_id': self.project.id,
            'title': 'Updated Test Schedule Task',
            'status': 'IN_PROGRESS',
            'priority': 'URGENT',
            'estimated_hours': '12.0',
            'start_date': '2026-10-17T09:00',
            'due_date': '2026-10-17T21:00',
            'assigned_to_ids': [self.user1.id, self.user2.id],
            'description': 'Updated schedule'
        }
        res_edit = self.client.post(f'/projects/tasks/{task.id}/edit/', edit_data)
        self.assertEqual(res_edit.status_code, 302)

        task.refresh_from_db()
        self.assertEqual(task.title, 'Updated Test Schedule Task')
        self.assertEqual(float(task.estimated_hours), 12.0)
        local_due_updated = timezone.localtime(task.due_date)
        self.assertEqual(local_due_updated.hour, 21)
        self.assertEqual(local_due_updated.minute, 0)


class TaskReviewAndAuditWorkflowTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.company = Company.objects.create(name="ByteBoot Inc", code="BYTE")

        # Admin user
        self.admin = User.objects.create_superuser('admin_user', 'admin@example.com', 'pass123')
        CompanyMembership.objects.create(user=self.admin, company=self.company, role='COMPANY_ADMIN')

        # Employee 1 (Assigned Developer)
        self.emp1 = User.objects.create_user('emp1', 'emp1@example.com', 'pass123')
        CompanyMembership.objects.create(user=self.emp1, company=self.company, role='STAFF')

        # Employee 2 (Task Creator / Non-Admin)
        self.emp2 = User.objects.create_user('emp2', 'emp2@example.com', 'pass123')
        CompanyMembership.objects.create(user=self.emp2, company=self.company, role='STAFF')

        # Project
        self.project = Project.objects.create(
            company=self.company, code="PRJ-200", name="Audit & Review Test Project", start_date="2026-01-01"
        )
        from projects.models import ProjectMember
        ProjectMember.objects.create(project=self.project, user=self.emp1, role='DEVELOPER')
        ProjectMember.objects.create(project=self.project, user=self.emp2, role='DEVELOPER')

        # Task 1: Reporter is Admin, Assigned to Emp 1
        self.task1 = Task.objects.create(
            company=self.company,
            project=self.project,
            task_number="TSK-701",
            title="Implement OAuth Login",
            status="TO_DO",
            assigned_to=self.emp1,
            reporter=self.admin
        )
        self.task1.assignees.set([self.emp1])

        # Task 2: Reporter is Emp 2 (non-admin creator), Assigned to Emp 1
        self.task2 = Task.objects.create(
            company=self.company,
            project=self.project,
            task_number="TSK-702",
            title="Write Documentation",
            status="TO_DO",
            assigned_to=self.emp1,
            reporter=self.emp2
        )
        self.task2.assignees.set([self.emp1])

    def test_employee_can_change_status_to_progress_review_rejected(self):
        from projects.models import TaskAuditLog
        self.client.login(username='emp1', password='pass123')
        session = self.client.session
        session['active_company_id'] = self.company.id
        session.save()

        # 1. Emp 1 transitions to IN_PROGRESS
        res1 = self.client.post(f'/projects/tasks/{self.task1.id}/', {
            'status': 'IN_PROGRESS',
            'status_comment': 'Started coding OAuth integration.'
        })
        self.assertRedirects(res1, f'/projects/tasks/{self.task1.id}/')
        self.task1.refresh_from_db()
        self.assertEqual(self.task1.status, 'IN_PROGRESS')

        # 2. Emp 1 submits for review (IN_REVIEW)
        res2 = self.client.post(f'/projects/tasks/{self.task1.id}/', {
            'status': 'IN_REVIEW',
            'status_comment': 'OAuth PR submitted, ready for QA review.'
        })
        self.assertRedirects(res2, f'/projects/tasks/{self.task1.id}/')
        self.task1.refresh_from_db()
        self.assertEqual(self.task1.status, 'IN_REVIEW')

        # 3. Emp 1 transitions to REJECTED (e.g. invalid task or cannot reproduce)
        res3 = self.client.post(f'/projects/tasks/{self.task1.id}/', {
            'status': 'REJECTED',
            'status_comment': 'Dependency incompatible with system.'
        })
        self.assertRedirects(res3, f'/projects/tasks/{self.task1.id}/')
        self.task1.refresh_from_db()
        self.assertEqual(self.task1.status, 'REJECTED')

        # 4. Verify TaskAuditLog entries
        audit_entries = TaskAuditLog.objects.filter(task=self.task1).order_by('created_at')
        actions = [a.action for a in audit_entries]
        self.assertIn('STATUS_CHANGE', actions)
        self.assertIn('SUBMIT_REVIEW', actions)
        self.assertIn('REJECTED', actions)

        # Check comment in SUBMIT_REVIEW entry
        review_entry = audit_entries.filter(action='SUBMIT_REVIEW').first()
        self.assertEqual(review_entry.from_status, 'IN_PROGRESS')
        self.assertEqual(review_entry.to_status, 'IN_REVIEW')
        self.assertIn('OAuth PR submitted', review_entry.comment)

    def test_employee_cannot_mark_task_as_done(self):
        from projects.models import TaskAuditLog
        self.client.login(username='emp1', password='pass123')
        session = self.client.session
        session['active_company_id'] = self.company.id
        session.save()

        # Emp 1 attempts to set status to DONE on task1 (where reporter is admin)
        res = self.client.post(f'/projects/tasks/{self.task1.id}/', {
            'status': 'DONE',
            'status_comment': 'Trying to mark it done myself'
        })
        self.assertRedirects(res, f'/projects/tasks/{self.task1.id}/')

        # Status must still be TO_DO
        self.task1.refresh_from_db()
        self.assertEqual(self.task1.status, 'TO_DO')
        self.assertIsNone(self.task1.completed_at)

        # No DONE audit log created
        done_logs = TaskAuditLog.objects.filter(task=self.task1, action='APPROVED_DONE')
        self.assertEqual(done_logs.count(), 0)

    def test_admin_can_approve_and_mark_done(self):
        from projects.models import TaskAuditLog
        self.client.login(username='admin_user', password='pass123')
        session = self.client.session
        session['active_company_id'] = self.company.id
        session.save()

        # Admin approves task1 to DONE
        res = self.client.post(f'/projects/tasks/{self.task1.id}/', {
            'status': 'DONE',
            'review_comment': 'Code reviewed and tested in staging. Excellent work!'
        })
        self.assertRedirects(res, f'/projects/tasks/{self.task1.id}/')

        self.task1.refresh_from_db()
        self.assertEqual(self.task1.status, 'DONE')
        self.assertIsNotNone(self.task1.completed_at)

        # Check TaskAuditLog
        audit = TaskAuditLog.objects.filter(task=self.task1, action='APPROVED_DONE').first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.user, self.admin)
        self.assertEqual(audit.to_status, 'DONE')
        self.assertIn('Code reviewed and tested', audit.comment)

    def test_task_creator_non_admin_can_approve_and_mark_done(self):
        from projects.models import TaskAuditLog
        # Emp 2 created task2. Emp 1 is assigned.
        # Emp 2 (non-admin creator) logs in
        self.client.login(username='emp2', password='pass123')
        session = self.client.session
        session['active_company_id'] = self.company.id
        session.save()

        # Emp 2 marks task2 as DONE
        res = self.client.post(f'/projects/tasks/{self.task2.id}/', {
            'status': 'DONE',
            'review_comment': 'Docs verified and complete.'
        })
        self.assertRedirects(res, f'/projects/tasks/{self.task2.id}/')

        self.task2.refresh_from_db()
        self.assertEqual(self.task2.status, 'DONE')
        self.assertIsNotNone(self.task2.completed_at)

        audit = TaskAuditLog.objects.filter(task=self.task2, action='APPROVED_DONE').first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.user, self.emp2)

    def test_work_log_and_comment_in_audit_trail(self):
        from projects.models import TaskAuditLog, WorkLog
        self.client.login(username='emp1', password='pass123')
        session = self.client.session
        session['active_company_id'] = self.company.id
        session.save()

        res = self.client.post(f'/projects/tasks/{self.task1.id}/', {
            'hours_spent': '4.5',
            'log_description': 'Implemented unit tests for login controller'
        })
        self.assertRedirects(res, f'/projects/tasks/{self.task1.id}/')

        # Check WorkLog
        wl = WorkLog.objects.filter(task=self.task1).first()
        self.assertIsNotNone(wl)
        self.assertEqual(float(wl.hours_spent), 4.5)

        # Check Audit log
        audit = TaskAuditLog.objects.filter(task=self.task1, action='WORK_LOGGED').first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.user, self.emp1)
        self.assertIn('4.5 hours', audit.comment)


class ProjectChatNotificationsAndOverdueEscalationTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.company = Company.objects.create(name="ByteBoot Inc", code="BYTE")

        self.admin = User.objects.create_superuser('admin_user', 'admin@example.com', 'pass123')
        CompanyMembership.objects.create(user=self.admin, company=self.company, role='COMPANY_ADMIN')

        self.emp1 = User.objects.create_user('emp1', 'emp1@example.com', 'pass123')
        CompanyMembership.objects.create(user=self.emp1, company=self.company, role='STAFF')

        self.project = Project.objects.create(
            company=self.company, code="PRJ-300", name="Chat Notification Project", start_date="2026-01-01",
            manager=self.admin
        )
        from projects.models import ProjectMember
        ProjectMember.objects.create(project=self.project, user=self.emp1, role='DEVELOPER')

    def test_task_lifecycle_notifies_team_chat(self):
        from projects.models import ProjectMessage
        self.client.login(username='admin_user', password='pass123')
        session = self.client.session
        session['active_company_id'] = self.company.id
        session.save()

        # 1. Create a task -> should post to project team chat
        res_create = self.client.post('/projects/tasks/create/', {
            'project_id': self.project.id,
            'title': 'Build Payment Gateway',
            'status': 'TO_DO',
            'priority': 'HIGH',
            'estimated_hours': '10.0',
            'assigned_to_id': self.emp1.id,
            'description': 'Integration with Stripe'
        })
        self.assertEqual(res_create.status_code, 302)

        task = Task.objects.get(title='Build Payment Gateway')
        chat_msg = ProjectMessage.objects.filter(project=self.project, related_entity_id=task.task_number).last()
        self.assertIsNotNone(chat_msg)
        self.assertEqual(chat_msg.message_type, 'TASK_UPDATE')
        self.assertIn('New Task Created', chat_msg.message_text)
        self.assertIn('@emp1', chat_msg.message_text)

        # 2. Emp1 logs in and moves task to IN_PROGRESS
        self.client.login(username='emp1', password='pass123')
        session = self.client.session
        session['active_company_id'] = self.company.id
        session.save()

        res_prog = self.client.post(f'/projects/tasks/{task.id}/', {
            'status': 'IN_PROGRESS',
            'status_comment': 'Setting up Stripe webhooks'
        })
        self.assertEqual(res_prog.status_code, 302)

        chat_msg2 = ProjectMessage.objects.filter(project=self.project, related_entity_id=task.task_number).last()
        self.assertIn('In Progress', chat_msg2.message_text)
        self.assertIn('Setting up Stripe webhooks', chat_msg2.message_text)

        # 3. Emp1 submits for review
        res_rev = self.client.post(f'/projects/tasks/{task.id}/', {
            'status': 'IN_REVIEW',
            'status_comment': 'Webhooks live on staging, ready for QA review.'
        })
        self.assertEqual(res_rev.status_code, 302)

        chat_msg3 = ProjectMessage.objects.filter(project=self.project, related_entity_id=task.task_number).last()
        self.assertIn('Submit for Review', chat_msg3.message_text)
        self.assertIn('Webhooks live on staging', chat_msg3.message_text)

        # 4. Admin logs in and approves to DONE
        self.client.login(username='admin_user', password='pass123')
        session = self.client.session
        session['active_company_id'] = self.company.id
        session.save()

        res_done = self.client.post(f'/projects/tasks/{task.id}/', {
            'status': 'DONE',
            'review_comment': 'Payment charges verified. Approved!'
        })
        self.assertEqual(res_done.status_code, 302)

        chat_msg4 = ProjectMessage.objects.filter(project=self.project, related_entity_id=task.task_number).last()
        self.assertIn('Task Approved & Done', chat_msg4.message_text)
        self.assertIn('Payment charges verified', chat_msg4.message_text)

    def test_overdue_task_escalates_and_mentions_members_in_chat(self):
        from projects.models import ProjectMessage
        from projects.notifications import check_and_escalate_overdue_tasks
        from datetime import timedelta
        from django.utils import timezone

        # Create an overdue task (due 3 hours ago)
        overdue_due_date = timezone.now() - timedelta(hours=3)
        task = Task.objects.create(
            company=self.company,
            project=self.project,
            task_number="TSK-999",
            title="Overdue Security Audit",
            status="IN_PROGRESS",
            priority="URGENT",
            due_date=overdue_due_date,
            assigned_to=self.emp1,
            reporter=self.admin
        )
        task.assignees.set([self.emp1])

        self.assertFalse(task.is_escalated)

        # Run escalation check
        escalated_list = check_and_escalate_overdue_tasks(company=self.company)
        self.assertEqual(len(escalated_list), 1)

        task.refresh_from_db()
        self.assertTrue(task.is_escalated)
        self.assertIsNotNone(task.overdue_escalated_at)

        # Verify chat escalation message
        msg = ProjectMessage.objects.filter(project=self.project, message_type='ESCALATION').first()
        self.assertIsNotNone(msg)
        self.assertIn('[OVERDUE ESCALATION]', msg.message_text)
        self.assertIn('TSK-999', msg.message_text)
        self.assertIn('@emp1', msg.message_text)
        self.assertIn('Overdue Security Audit', msg.message_text)

        # Re-running check does not create duplicate escalation message within 24h
        initial_msg_count = ProjectMessage.objects.filter(project=self.project, message_type='ESCALATION').count()
        check_and_escalate_overdue_tasks(company=self.company)
        new_msg_count = ProjectMessage.objects.filter(project=self.project, message_type='ESCALATION').count()
        self.assertEqual(initial_msg_count, new_msg_count)

        # When admin marks task as DONE, is_escalated is cleared
        self.client.login(username='admin_user', password='pass123')
        session = self.client.session
        session['active_company_id'] = self.company.id
        session.save()

        res_done = self.client.post(f'/projects/tasks/{task.id}/', {
            'status': 'DONE',
            'review_comment': 'Completed late but approved'
        })
        self.assertEqual(res_done.status_code, 302)

        task.refresh_from_db()
        self.assertEqual(task.status, 'DONE')
        self.assertFalse(task.is_escalated)
        self.assertIsNone(task.overdue_escalated_at)


class AutomatedEntityDropdownTest(TestCase):
    def setUp(self):
        from crm.models import Customer, Lead
        from invoicing.models import Invoice
        from datetime import date
        from projects.models import ProjectMessage

        self.client = Client()
        self.company = Company.objects.create(name="ByteBoot Inc", code="BYTE")
        self.admin = User.objects.create_superuser('admin_user', 'admin@example.com', 'pass123')
        CompanyMembership.objects.create(user=self.admin, company=self.company, role='COMPANY_ADMIN')

        self.client.login(username='admin_user', password='pass123')
        session = self.client.session
        session['active_company_id'] = self.company.id
        session.save()

        self.project_a = Project.objects.create(
            company=self.company, code="PRJ-101", name="Project Alpha", start_date="2026-01-01", manager=self.admin
        )
        self.project_b = Project.objects.create(
            company=self.company, code="PRJ-102", name="Project Beta", start_date="2026-01-01", manager=self.admin
        )

        # 15 tasks for Project Alpha
        for i in range(1, 16):
            Task.objects.create(
                company=self.company,
                project=self.project_a,
                task_number=f"TSK-A{i:02d}",
                title=f"Task Alpha {i}",
                status='IN_PROGRESS',
                priority='MEDIUM',
                reporter=self.admin
            )

        # 3 tasks for Project Beta
        for i in range(1, 4):
            Task.objects.create(
                company=self.company,
                project=self.project_b,
                task_number=f"TSK-B{i:02d}",
                title=f"Task Beta {i}",
                status='TODO',
                priority='LOW',
                reporter=self.admin
            )

        # 15 CRM leads
        for i in range(1, 16):
            Lead.objects.create(
                company=self.company,
                lead_number=f"LD-{10000 + i}",
                company_name=f"Lead Corp {i}",
                contact_person=f"Contact Person {i}",
                phone=f"98765432{i:02d}",
                status='QUALIFIED'
            )

        # 1 customer & 15 Invoices
        self.customer = Customer.objects.create(
            company=self.company,
            customer_code="CUST-101",
            name="Acme Corp",
            email="billing@acme.com",
            phone="1234567890",
            billing_address="123 Tech Park",
            city="Bengaluru"
        )
        for i in range(1, 16):
            Invoice.objects.create(
                company=self.company,
                invoice_number=f"INV-{2000 + i}",
                customer=self.customer,
                date=date.today(),
                due_date=date.today(),
                grand_total=1000 * i,
                balance_due=500 * i,
                status='ISSUED'
            )

    def test_task_search_distribution_and_project_scoping(self):
        # 1. Fetch page 1: should return exactly 10 tasks for Project Alpha
        res1 = self.client.get(f'/projects/api/entities/search/?entity_type=TASK&project_id={self.project_a.id}&page=1&page_size=10')
        self.assertEqual(res1.status_code, 200)
        data1 = res1.json()
        self.assertEqual(len(data1['results']), 10)
        self.assertEqual(data1['page'], 1)
        self.assertTrue(data1['has_more'])
        self.assertEqual(data1['total'], 15)
        # Verify no tasks from Project Beta leaked in
        for item in data1['results']:
            self.assertTrue(item['number'].startswith('TSK-A'))

        # 2. Fetch page 2: should return remaining 5 tasks
        res2 = self.client.get(f'/projects/api/entities/search/?entity_type=TASK&project_id={self.project_a.id}&page=2&page_size=10')
        self.assertEqual(res2.status_code, 200)
        data2 = res2.json()
        self.assertEqual(len(data2['results']), 5)
        self.assertEqual(data2['page'], 2)
        self.assertFalse(data2['has_more'])

        # 3. Live search query filter
        res_filter = self.client.get(f'/projects/api/entities/search/?entity_type=TASK&project_id={self.project_a.id}&q=Task Alpha 12')
        self.assertEqual(res_filter.status_code, 200)
        data_filter = res_filter.json()
        self.assertEqual(len(data_filter['results']), 1)
        self.assertEqual(data_filter['results'][0]['number'], 'TSK-A12')

    def test_lead_search_distribution_and_filtering(self):
        # Page 1: 10 items
        res1 = self.client.get('/projects/api/entities/search/?entity_type=LEAD&page=1&page_size=10')
        self.assertEqual(res1.status_code, 200)
        data1 = res1.json()
        self.assertEqual(len(data1['results']), 10)
        self.assertTrue(data1['has_more'])
        self.assertEqual(data1['total'], 15)

        # Page 2: 5 items
        res2 = self.client.get('/projects/api/entities/search/?entity_type=LEAD&page=2&page_size=10')
        self.assertEqual(res2.status_code, 200)
        data2 = res2.json()
        self.assertEqual(len(data2['results']), 5)
        self.assertFalse(data2['has_more'])

        # Live filter
        res_filter = self.client.get('/projects/api/entities/search/?entity_type=LEAD&q=Lead Corp 14')
        self.assertEqual(res_filter.status_code, 200)
        data_filter = res_filter.json()
        self.assertEqual(len(data_filter['results']), 1)
        self.assertEqual(data_filter['results'][0]['number'], 'LD-10014')

    def test_invoice_search_distribution_and_filtering(self):
        # Page 1: 10 items
        res1 = self.client.get('/projects/api/entities/search/?entity_type=INVOICE&page=1&page_size=10')
        self.assertEqual(res1.status_code, 200)
        data1 = res1.json()
        self.assertEqual(len(data1['results']), 10)
        self.assertTrue(data1['has_more'])
        self.assertEqual(data1['total'], 15)

        # Page 2: 5 items
        res2 = self.client.get('/projects/api/entities/search/?entity_type=INVOICE&page=2&page_size=10')
        self.assertEqual(res2.status_code, 200)
        data2 = res2.json()
        self.assertEqual(len(data2['results']), 5)
        self.assertFalse(data2['has_more'])

        # Live filter
        res_filter = self.client.get('/projects/api/entities/search/?entity_type=INVOICE&q=INV-2015')
        self.assertEqual(res_filter.status_code, 200)
        data_filter = res_filter.json()
        self.assertEqual(len(data_filter['results']), 1)
        self.assertEqual(data_filter['results'][0]['number'], 'INV-2015')

    def test_entity_lookups_and_chat_message_linking(self):
        from projects.models import ProjectMessage

        # Task lookup redirect
        res_task = self.client.get('/projects/tasks/lookup/TSK-A01/')
        self.assertEqual(res_task.status_code, 302)
        task = Task.objects.get(task_number='TSK-A01')
        self.assertIn(f'/projects/tasks/{task.id}/', res_task.url)

        # Invoice lookup redirect
        res_inv = self.client.get('/projects/invoices/lookup/INV-2001/')
        self.assertEqual(res_inv.status_code, 302)
        from invoicing.models import Invoice
        inv = Invoice.objects.get(invoice_number='INV-2001')
        self.assertIn(f'/invoicing/invoices/{inv.id}/', res_inv.url)

        # Lead lookup redirect
        res_lead = self.client.get('/projects/leads/lookup/LD-10001/')
        self.assertEqual(res_lead.status_code, 302)
        from crm.models import Lead
        lead = Lead.objects.get(lead_number='LD-10001')
        self.assertIn(f'/crm/leads/{lead.id}/', res_lead.url)

        # Send chat message with linked task
        res_msg = self.client.post(f'/projects/conversations/?project_id={self.project_a.id}', {
            'message_text': 'Check out this linked task for review!',
            'entity_type': 'TASK',
            'entity_id': 'TSK-A01',
            'entity_label': 'TSK-A01 — Task Alpha 1'
        })
        self.assertEqual(res_msg.status_code, 302)

        saved_msg = ProjectMessage.objects.filter(project=self.project_a, related_entity_type='TASK').first()
        self.assertIsNotNone(saved_msg)
        self.assertEqual(saved_msg.related_entity_id, 'TSK-A01')
        self.assertEqual(saved_msg.related_entity_label, 'TSK-A01 — Task Alpha 1')

    def test_permission_based_distribution(self):
        from projects.models import ProjectMember

        # 1. Create a non-member staff user (has staff role, but NOT assigned to Project Alpha)
        staff_non_member = User.objects.create_user('staff_non_member', 'nonmember@example.com', 'pass123')
        CompanyMembership.objects.create(user=staff_non_member, company=self.company, role='STAFF')

        # 2. Create a team member staff user (assigned to Project Alpha)
        staff_team_member = User.objects.create_user('staff_team_member', 'teammember@example.com', 'pass123')
        CompanyMembership.objects.create(user=staff_team_member, company=self.company, role='STAFF')
        ProjectMember.objects.create(project=self.project_a, user=staff_team_member, role='DEVELOPER')

        # 3. Create an accountant user (has invoicing permission, but NO crm and NO projects)
        accountant_user = User.objects.create_user('accountant_user', 'accountant@example.com', 'pass123')
        CompanyMembership.objects.create(user=accountant_user, company=self.company, role='ACCOUNTANT')

        # --- A. TASK PERMISSIONS: Only team members and admins can distribute tasks ---
        # Non-member requests tasks for Project Alpha -> 403 Forbidden
        self.client.login(username='staff_non_member', password='pass123')
        session = self.client.session
        session['active_company_id'] = self.company.id
        session.save()
        res_task_denied = self.client.get(f'/projects/api/entities/search/?entity_type=TASK&project_id={self.project_a.id}')
        self.assertEqual(res_task_denied.status_code, 403)

        # Team member requests tasks for Project Alpha -> 200 OK
        self.client.login(username='staff_team_member', password='pass123')
        session = self.client.session
        session['active_company_id'] = self.company.id
        session.save()
        res_task_allowed = self.client.get(f'/projects/api/entities/search/?entity_type=TASK&project_id={self.project_a.id}')
        self.assertEqual(res_task_allowed.status_code, 200)
        self.assertEqual(len(res_task_allowed.json()['results']), 10)

        # --- B. INVOICE PERMISSIONS: Only users with invoicing permission or admins can distribute invoices ---
        # Staff member (no invoicing permission) requests invoices -> 403 Forbidden
        res_inv_denied = self.client.get('/projects/api/entities/search/?entity_type=INVOICE')
        self.assertEqual(res_inv_denied.status_code, 403)

        # Accountant (has invoicing permission) requests invoices -> 200 OK
        self.client.login(username='accountant_user', password='pass123')
        session = self.client.session
        session['active_company_id'] = self.company.id
        session.save()
        res_inv_allowed = self.client.get('/projects/api/entities/search/?entity_type=INVOICE')
        self.assertEqual(res_inv_allowed.status_code, 200)
        self.assertEqual(len(res_inv_allowed.json()['results']), 10)

        # --- C. LEAD PERMISSIONS: Only users with crm permission or admins can distribute leads ---
        # Accountant (no crm permission) requests leads -> 403 Forbidden
        res_lead_denied = self.client.get('/projects/api/entities/search/?entity_type=LEAD')
        self.assertEqual(res_lead_denied.status_code, 403)

        # Staff member (has crm permission) requests leads -> 200 OK
        self.client.login(username='staff_team_member', password='pass123')
        session = self.client.session
        session['active_company_id'] = self.company.id
        session.save()
        res_lead_allowed = self.client.get('/projects/api/entities/search/?entity_type=LEAD')
        self.assertEqual(res_lead_allowed.status_code, 200)
        self.assertEqual(len(res_lead_allowed.json()['results']), 10)

    def test_conversations_sidebar_closed_and_initial_blank_state(self):
        self.client.login(username='admin_user', password='pass123')
        session = self.client.session
        session['active_company_id'] = self.company.id
        session.save()

        res = self.client.get('/projects/conversations/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context['active_tab'], 'chat')
        # On initial loading, no project is focused/selected
        self.assertIsNone(res.context['active_project'])
        content = res.content.decode('utf-8')
        self.assertIn("sidebarOpen: false", content)
        self.assertIn("Byteboot Team Chat", content)


from django.test import TransactionTestCase

class ProjectWebsocketChatAsyncTest(TransactionTestCase):
    def setUp(self):
        self.company = Company.objects.create(name="WS Co", code="WSCO")
        self.user = User.objects.create_superuser('ws_admin', 'wsadmin@example.com', 'pass123')
        CompanyMembership.objects.create(user=self.user, company=self.company, role='COMPANY_ADMIN')
        self.project = Project.objects.create(
            company=self.company, code="PRJ-WS1", name="WS Project", start_date="2026-01-01", manager=self.user
        )

    async def test_websocket_chat_communication(self):
        from byteboot_day.asgi import application
        from channels.testing import WebsocketCommunicator
        from asgiref.sync import sync_to_async
        from projects.models import ProjectMessage

        communicator = WebsocketCommunicator(application, f'/ws/projects/{self.project.id}/chat/')
        communicator.scope['user'] = self.user

        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        handshake = await communicator.receive_json_from()
        self.assertEqual(handshake['type'], 'connection_established')
        self.assertEqual(handshake['project_id'], self.project.id)

        # Send a chat message over WebSocket
        await communicator.send_json_to({
            'message_text': 'Real-time WebSocket team discussion message',
            'entity_type': 'NONE'
        })

        broadcast = await communicator.receive_json_from()
        self.assertEqual(broadcast['type'], 'chat_message')
        self.assertEqual(broadcast['message']['message_text'], 'Real-time WebSocket team discussion message')

        # Verify database record
        saved = await sync_to_async(
            lambda: ProjectMessage.objects.filter(
                project=self.project,
                message_text='Real-time WebSocket team discussion message'
            ).first()
        )()
        self.assertIsNotNone(saved)

        await communicator.disconnect()

    async def test_websocket_unauthorized_access(self):
        from byteboot_day.asgi import application
        from channels.testing import WebsocketCommunicator
        from asgiref.sync import sync_to_async

        # Create an unassigned user
        unassigned_user = await sync_to_async(User.objects.create_user)('unassigned_ws_user', 'u@test.com', 'pass123')
        await sync_to_async(CompanyMembership.objects.create)(user=unassigned_user, company=self.company, role='STAFF')

        communicator = WebsocketCommunicator(application, f'/ws/projects/{self.project.id}/chat/')
        communicator.scope['user'] = unassigned_user

        connected, close_code = await communicator.connect()
        self.assertTrue(not connected or close_code == 4003)
        await communicator.disconnect()






