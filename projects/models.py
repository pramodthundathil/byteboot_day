from django.db import models
from django.contrib.auth.models import User
from decimal import Decimal
from tenants.models import Company
from crm.models import Customer

class Project(models.Model):
    STATUS_CHOICES = (
        ('PLANNING', 'Planning'),
        ('IN_PROGRESS', 'In Progress'),
        ('ON_HOLD', 'On Hold'),
        ('COMPLETED', 'Completed'),
        ('CANCELLED', 'Cancelled'),
    )

    PRIORITY_CHOICES = (
        ('LOW', 'Low'),
        ('MEDIUM', 'Medium'),
        ('HIGH', 'High'),
        ('URGENT', 'Urgent'),
    )

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='projects')
    code = models.CharField(max_length=50, help_text="e.g. PRJ-101")
    name = models.CharField(max_length=255)
    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, blank=True, related_name='projects')
    description = models.TextField(blank=True)
    
    start_date = models.DateField()
    target_end_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='IN_PROGRESS')
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default='MEDIUM')
    
    manager = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='managed_projects')
    budget = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    estimated_hours = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('company', 'code')
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.code} - {self.name}"

    def total_logged_hours(self):
        return sum((t.total_actual_hours() for t in self.tasks.all()), Decimal('0.00'))

    def is_user_assigned(self, user):
        if not user or not user.is_authenticated:
            return False
        return (self.manager_id == user.id) or self.members.filter(user=user).exists()


class ProjectMember(models.Model):
    ROLE_CHOICES = (
        ('MANAGER', 'Project Manager'),
        ('DEVELOPER', 'Developer'),
        ('DESIGNER', 'Designer'),
        ('QA', 'QA Engineer'),
        ('VIEWER', 'Viewer'),
    )

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='members')
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='DEVELOPER')

    class Meta:
        unique_together = ('project', 'user')

    def __str__(self):
        return f"{self.user.username} in {self.project.code} as {self.get_role_display()}"


class Task(models.Model):
    STATUS_CHOICES = (
        ('BACKLOG', 'Backlog'),
        ('TO_DO', 'To Do'),
        ('IN_PROGRESS', 'In Progress'),
        ('IN_REVIEW', 'In Review'),
        ('BLOCKED', 'Blocked'),
        ('DONE', 'Done'),
    )

    PRIORITY_CHOICES = (
        ('LOW', 'Low'),
        ('MEDIUM', 'Medium'),
        ('HIGH', 'High'),
        ('URGENT', 'Urgent'),
    )

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='tasks')
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='tasks')
    task_number = models.CharField(max_length=50, help_text="e.g. TSK-501")
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    acceptance_criteria = models.TextField(blank=True)
    
    assigned_to = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_tasks')
    assignees = models.ManyToManyField(User, blank=True, related_name='tasks_assigned')
    reporter = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='reported_tasks')
    parent_task = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='subtasks')
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='TO_DO')
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default='MEDIUM')
    
    estimated_hours = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal('0.00'))
    start_date = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('company', 'task_number')
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.task_number}: {self.title} [{self.status}]"

    def total_actual_hours(self):
        return sum((wl.hours_spent for wl in self.work_logs.all()), Decimal('0.00'))

    def get_all_assignees(self):
        assigned_list = list(self.assignees.all())
        if self.assigned_to and self.assigned_to not in assigned_list:
            assigned_list.insert(0, self.assigned_to)
        return assigned_list


class WorkLog(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='work_logs')
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='work_logs')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='work_logs')
    work_date = models.DateField()
    hours_spent = models.DecimalField(max_digits=5, decimal_places=2)
    description = models.TextField()
    is_billable = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-work_date', '-created_at']

    def __str__(self):
        return f"{self.user.username} logged {self.hours_spent}h on {self.task.task_number}"


class ProjectMessage(models.Model):
    ENTITY_TYPE_CHOICES = (
        ('NONE', 'None'),
        ('TASK', 'Task'),
        ('INVOICE', 'Invoice'),
        ('LEAD', 'Lead'),
        ('EMPLOYEE', 'Employee'),
        ('CUSTOMER', 'Customer'),
    )

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='messages')
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name='project_messages')
    message_text = models.TextField()
    parent = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='replies')
    
    # Entity Linking (WhatsApp-style task / invoice / lead tagging)
    related_entity_type = models.CharField(max_length=20, choices=ENTITY_TYPE_CHOICES, default='NONE')
    related_entity_id = models.CharField(max_length=100, blank=True, help_text="e.g. TSK-501 or INV-1001")
    related_entity_label = models.CharField(max_length=255, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"Message by {self.sender.username} in {self.project.code} at {self.created_at.strftime('%H:%M')}"
