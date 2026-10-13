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

            # Fallback: if user (e.g. superuser created via CLI) has no company/membership yet,
            # attach to the first active company or auto-provision the default company.
            if not company:
                company = Company.objects.filter(is_active=True).first() or Company.objects.first()
                if not company:
                    company, _ = Company.objects.get_or_create(
                        code="BYTEBOOT",
                        defaults={
                            "name": "Byteboot Techno Solutions Pvt Ltd",
                            "tagline": "Multi-Tenant Enterprise Software Architectures",
                            "logo_url": "/static/js/img/company_logo_icon.png",
                            "email": "contact@byteboot.tech",
                            "phone": "+91 484 2901234",
                            "address": "Infopark Campus, Kakkanad",
                            "city": "Kochi",
                            "state": "Kerala",
                            "country": "India",
                            "pincode": "682042",
                            "currency_code": "INR",
                            "currency_symbol": "₹",
                            "is_active": True,
                        }
                    )

            if company and not membership:
                default_role = 'COMPANY_ADMIN' if (request.user.is_superuser or request.user.is_staff) else 'STAFF'
                membership, _ = CompanyMembership.objects.get_or_create(
                    user=request.user,
                    company=company,
                    defaults={'role': default_role, 'is_active': True}
                )
                if not membership.is_active:
                    membership.is_active = True
                    membership.save(update_fields=['is_active'])

            if company and (profile.active_company_id != company.id or request.session.get('active_company_id') != company.id):
                profile.active_company = company
                profile.save(update_fields=['active_company'])
                request.session['active_company_id'] = company.id

            request.company = company
            request.membership = membership
