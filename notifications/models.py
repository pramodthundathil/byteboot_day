from django.db import models
from django.contrib.auth.models import User
from tenants.models import Company

class Notification(models.Model):
    CATEGORY_CHOICES = (
        ('LEAD', 'CRM / Lead'),
        ('INVOICE', 'Invoice / Payment'),
        ('TASK', 'Task / Project'),
        ('LEAVE', 'HR / Leave'),
        ('APPROVAL', 'Approval Request'),
        ('SYSTEM', 'System Alert'),
    )

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='notifications')
    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    title = models.CharField(max_length=255)
    message = models.TextField()
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='SYSTEM')
    action_url = models.CharField(max_length=500, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Notification for {self.recipient.username}: {self.title}"


class ApprovalRequest(models.Model):
    STATUS_CHOICES = (
        ('PENDING', 'Pending'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
    )

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='approval_requests')
    requester = models.ForeignKey(User, on_delete=models.CASCADE, related_name='requested_approvals')
    module = models.CharField(max_length=50, help_text="e.g. leave, invoice, journal")
    object_type = models.CharField(max_length=100)
    object_id = models.CharField(max_length=100)
    
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    required_role = models.CharField(max_length=50, default='COMPANY_ADMIN')
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    decided_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='decided_approvals')
    decided_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Approval [{self.status}] {self.title} by {self.requester.username}"
