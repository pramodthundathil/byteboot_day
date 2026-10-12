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

