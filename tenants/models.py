import json
from django.db import models
from django.contrib.auth.models import User
from django.utils.translation import gettext_lazy as _

class Company(models.Model):
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=50, unique=True, help_text="Short code e.g. BYTEBOOT")
    tagline = models.CharField(max_length=255, blank=True)
    logo_url = models.CharField(max_length=500, blank=True, help_text="Logo image URL or path")
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    website = models.URLField(blank=True)
    
    address = models.TextField(blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, default="India")
    pincode = models.CharField(max_length=20, blank=True)
    gstin = models.CharField(max_length=20, blank=True, help_text="15-digit GSTIN")
    registration_number = models.CharField(max_length=100, blank=True)
    
    currency_code = models.CharField(max_length=10, default="INR")
    currency_symbol = models.CharField(max_length=10, default="₹")
    financial_year_start_month = models.IntegerField(default=4, help_text="1=Jan, 4=Apr")
    
    is_active = models.BooleanField(default=True)
    config = models.JSONField(default=dict, blank=True, help_text="Invoice formats, default bank, etc.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "Companies"
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.code})"

    def get_config(self, key, default=None):
        if not isinstance(self.config, dict):
            return default
        return self.config.get(key, default)


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    phone = models.CharField(max_length=30, blank=True)
    avatar_url = models.CharField(max_length=500, blank=True)
    designation = models.CharField(max_length=100, blank=True)
    is_platform_admin = models.BooleanField(default=False)
    active_company = models.ForeignKey(Company, on_delete=models.SET_NULL, null=True, blank=True, related_name='active_users')

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username}"


class CustomRole(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='custom_roles')
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    permissions = models.JSONField(default=dict, help_text="Granular permissions matrix: {module: {action: true}}")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('company', 'name')
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.company.code})"


class CompanyMembership(models.Model):
    ROLE_CHOICES = (
        ('PLATFORM_ADMIN', 'Platform Super Admin'),
        ('COMPANY_ADMIN', 'Company Admin'),
        ('ACCOUNTANT', 'Accountant'),
        ('HR', 'HR Manager'),
        ('MANAGER', 'Team Lead / Manager'),
        ('STAFF', 'Staff Member'),
        ('CUSTOM', 'Custom Role'),
    )

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='memberships')
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='memberships')
    role = models.CharField(max_length=30, choices=ROLE_CHOICES, default='STAFF')
    custom_role = models.ForeignKey(CustomRole, on_delete=models.SET_NULL, null=True, blank=True, related_name='memberships')
    custom_role_name = models.CharField(max_length=100, blank=True)
    department = models.CharField(max_length=100, blank=True)
    is_active = models.BooleanField(default=True)
    date_joined = models.DateTimeField(auto_now_add=True)
    permissions = models.JSONField(default=dict, blank=True, help_text="Custom granular overrides: {module: {action: true}}")

    class Meta:
        unique_together = ('user', 'company')
        ordering = ['company', 'user']

    def __str__(self):
        role_label = self.custom_role.name if (self.role == 'CUSTOM' and self.custom_role) else self.get_role_display()
        return f"{self.user.username} @ {self.company.code} ({role_label})"

    def has_permission(self, module, action='view'):
        if not self.is_active:
            return False

        # Platform Super Admin, Django Superuser & Company Admin have full unrestricted access
        if (self.user and self.user.is_superuser) or self.role in ('PLATFORM_ADMIN', 'COMPANY_ADMIN'):
            return True

        # Map alias module names for consistency
        mod_key = module.lower()
        if mod_key in ('employees', 'leave', 'payroll'):
            mod_key = 'hr'
        elif mod_key == 'tasks':
            mod_key = 'projects'

        # 1. Check explicit user-level custom permission overrides first (can be True or False)
        if isinstance(self.permissions, dict):
            for m in (module, mod_key):
                if m in self.permissions:
                    mod_perms = self.permissions.get(m, {})
                    if isinstance(mod_perms, dict) and action in mod_perms:
                        return bool(mod_perms[action])
                    elif isinstance(mod_perms, dict) and 'view' in mod_perms and action == 'view':
                        return bool(mod_perms['view'])

        # 2. Check custom role permissions if role is CUSTOM
        if self.role == 'CUSTOM' and self.custom_role and isinstance(self.custom_role.permissions, dict):
            for m in (module, mod_key):
                if m in self.custom_role.permissions:
                    mod_perms = self.custom_role.permissions.get(m, {})
                    if isinstance(mod_perms, dict) and action in mod_perms:
                        return bool(mod_perms[action])
                    elif isinstance(mod_perms, dict) and 'view' in mod_perms and action == 'view':
                        return bool(mod_perms['view'])
            return False

        # 3. Default built-in role permission fallback rules:
        if module in ('roles', 'tenants', 'company_settings'):
            return False

        if self.role == 'ACCOUNTANT':
            if mod_key in ('accounting', 'invoicing', 'vendors'):
                if action in ('view', 'create', 'edit', 'post', 'record_payment'):
                    return True
            return False
        elif self.role == 'HR':
            if mod_key == 'hr':
                if action in ('view', 'create', 'edit', 'approve'):
                    return True
            return False
        elif self.role == 'MANAGER':
            if mod_key in ('crm', 'projects', 'hr'):
                if action in ('view', 'create', 'edit', 'assign', 'approve', 'convert'):
                    return True
            return False
        elif self.role == 'STAFF':
            # Staff by default has view & edit access to projects/tasks, but NOT assign or approve
            if mod_key == 'projects':
                if action in ('view', 'create', 'edit'):
                    return True
            return False

        return False


class AuditEvent(models.Model):
    ACTION_CHOICES = (
        ('CREATE', 'Create'),
        ('UPDATE', 'Update'),
        ('DELETE', 'Delete'),
        ('POST', 'Post'),
        ('REVERSE', 'Reverse'),
        ('CONVERT', 'Convert'),
        ('APPROVE', 'Approve'),
        ('REJECT', 'Reject'),
        ('LOGIN', 'Login'),
    )

    company = models.ForeignKey(Company, on_delete=models.CASCADE, null=True, blank=True, related_name='audit_logs')
    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='audit_actions')
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    module = models.CharField(max_length=50)
    model_name = models.CharField(max_length=100)
    object_id = models.CharField(max_length=100, blank=True)
    object_repr = models.CharField(max_length=255, blank=True)
    changes = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"[{self.timestamp.strftime('%Y-%m-%d %H:%M')}] {self.actor} - {self.action} {self.model_name}#{self.object_id}"

    @classmethod
    def log(cls, company, actor, action, module, model_name, object_id, object_repr="", changes=None, ip_address=None):
        return cls.objects.create(
            company=company,
            actor=actor,
            action=action,
            module=module,
            model_name=model_name,
            object_id=str(object_id),
            object_repr=str(object_repr)[:255],
            changes=changes or {},
            ip_address=ip_address
        )
