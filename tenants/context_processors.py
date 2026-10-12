from tenants.models import CompanyMembership

def tenant_context(request):
    if not hasattr(request, 'user') or not request.user.is_authenticated:
        return {
            'current_company': None,
            'current_membership': None,
            'user_companies': [],
        }

    memberships = CompanyMembership.objects.filter(user=request.user, is_active=True).select_related('company')
    user_companies = [m.company for m in memberships if m.company.is_active]

    mem = getattr(request, 'membership', None)
    user_perms = {}
    if mem:
        for mod in ['crm', 'invoicing', 'accounting', 'hr', 'projects', 'roles']:
            user_perms[mod] = mem.has_permission(mod, 'view')

    return {
        'current_company': getattr(request, 'company', None),
        'current_membership': mem,
        'user_companies': user_companies,
        'user_perms': user_perms,
    }
