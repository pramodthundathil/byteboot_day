from django.utils.deprecation import MiddlewareMixin
from tenants.models import Company, CompanyMembership, UserProfile

class TenantMiddleware(MiddlewareMixin):
    """
    Middleware to resolve and inject request.company and request.membership.
    Guarantees strict tenant context across all requests.
    """
    def process_request(self, request):
        request.company = None
        request.membership = None
        
        if request.user.is_authenticated:
            # Ensure UserProfile exists
            profile, created = UserProfile.objects.get_or_create(user=request.user)
            
            # Check session override or profile active company
            session_company_id = request.session.get('active_company_id')
            company = None
            
            if session_company_id:
                try:
                    company = Company.objects.get(id=session_company_id, is_active=True)
                except Company.DoesNotExist:
                    company = None

            if not company and profile.active_company:
                company = profile.active_company

            # If still no company, fetch user's first available membership
            membership = None
            if company:
                membership = CompanyMembership.objects.filter(user=request.user, company=company, is_active=True).first()
            
            if not membership:
                membership = CompanyMembership.objects.filter(user=request.user, is_active=True).first()
                if membership:
                    company = membership.company
                    profile.active_company = company
                    profile.save(update_fields=['active_company'])
                    request.session['active_company_id'] = company.id

            request.company = company
            request.membership = membership
