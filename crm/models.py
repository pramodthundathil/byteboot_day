from django.db import models
from django.contrib.auth.models import User
from tenants.models import Company

class Lead(models.Model):
    STATUS_CHOICES = (
        ('NEW', 'New'),
        ('CONTACTED', 'Contacted'),
        ('FOLLOW_UP', 'Follow-up Required'),
        ('QUALIFIED', 'Qualified'),
        ('PROPOSAL_SENT', 'Proposal Sent'),
        ('NEGOTIATION', 'Negotiation'),
        ('WON', 'Won'),
        ('LOST', 'Lost'),
    )

    PRIORITY_CHOICES = (
        ('LOW', 'Low'),
        ('MEDIUM', 'Medium'),
        ('HIGH', 'High'),
        ('URGENT', 'Urgent'),
    )

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='leads')
    lead_number = models.CharField(max_length=50, help_text="e.g. LD-10001")
    company_name = models.CharField(max_length=255)
    contact_person = models.CharField(max_length=255)
    phone = models.CharField(max_length=50)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    
    source = models.CharField(max_length=100, default='Website', help_text="e.g. Website, Referral, Cold Call, Social Media")
    industry = models.CharField(max_length=100, blank=True)
    service_interested = models.CharField(max_length=255, help_text="e.g. Custom ERP, Web Development, Mobile App")
    estimated_value = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    
    assigned_to = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_leads')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='created_leads')
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default='MEDIUM')
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='NEW')
    pipeline_stage = models.CharField(max_length=50, default='New')
    conversion_probability = models.IntegerField(default=20, help_text="Percentage 0-100")
    lost_reason = models.TextField(blank=True)
    
    notes = models.TextField(blank=True)
    next_followup_date = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('company', 'lead_number')
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.lead_number} - {self.company_name} ({self.contact_person})"


class LeadFollowup(models.Model):
    METHOD_CHOICES = (
        ('PHONE', 'Phone Call'),
        ('EMAIL', 'Email'),
        ('MEETING', 'In-Person Meeting'),
        ('WHATSAPP', 'WhatsApp Message'),
        ('VIDEO', 'Video Call'),
    )

    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='followups')
    staff = models.ForeignKey(User, on_delete=models.CASCADE)
    method = models.CharField(max_length=20, choices=METHOD_CHOICES, default='PHONE')
    interaction_time = models.DateTimeField(auto_now_add=True)
    summary = models.TextField()
    outcome = models.CharField(max_length=255, blank=True)
    next_action = models.CharField(max_length=255, blank=True)
    next_followup_date = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-interaction_time']

    def __str__(self):
        return f"Followup for {self.lead.lead_number} by {self.staff.username}"


class Customer(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='customers')
    customer_code = models.CharField(max_length=50)
    name = models.CharField(max_length=255, help_text="Individual or Company Name")
    company_name = models.CharField(max_length=255, blank=True)
    email = models.EmailField()
    phone = models.CharField(max_length=50)
    
    billing_address = models.TextField()
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100)
    country = models.CharField(max_length=100, default='India')
    pincode = models.CharField(max_length=20, blank=True)
    gstin = models.CharField(max_length=20, blank=True)
    place_of_supply = models.CharField(max_length=100, blank=True, help_text="State code or name for GST")
    
    credit_limit = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    payment_terms_days = models.IntegerField(default=30)
    tax_exempt = models.BooleanField(default=False)
    
    lead_reference = models.ForeignKey(Lead, on_delete=models.SET_NULL, null=True, blank=True, related_name='converted_customer')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('company', 'customer_code')
        ordering = ['name']

    def __str__(self):
        return f"{self.customer_code} - {self.name}"


class CustomerContact(models.Model):
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='contacts')
    name = models.CharField(max_length=255)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    designation = models.CharField(max_length=100, blank=True)
    is_primary = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.name} ({self.customer.name})"
