from tenants.models import CompanyMembership
from django.db.models import Q

def tenant_context(request):
    if not hasattr(request, 'user') or not request.user.is_authenticated:
        return {
            'current_company': None,
            'current_membership': None,
            'user_companies': [],
            'unread_chat_count': 0,
        }

    memberships = CompanyMembership.objects.filter(user=request.user, is_active=True).select_related('company')
    user_companies = [m.company for m in memberships if m.company.is_active]

    mem = getattr(request, 'membership', None)
    user_perms = {}
    if request.user.is_superuser:
        for mod in ['crm', 'invoicing', 'accounting', 'hr', 'projects', 'roles']:
            user_perms[mod] = True
    elif mem:
        for mod in ['crm', 'invoicing', 'accounting', 'hr', 'projects', 'roles']:
            user_perms[mod] = mem.has_permission(mod, 'view')

    curr_company = getattr(request, 'company', None)
    unread_chat_count = 0
    if curr_company and user_perms.get('projects'):
        try:
            from projects.models import ProjectMessage
            base_q = Q(project__company=curr_company)
            if not request.user.is_superuser:
                base_q &= (Q(project__members__user=request.user) | Q(project__manager=request.user))
            unread_chat_count = ProjectMessage.objects.filter(base_q).exclude(
                sender=request.user
            ).exclude(
                read_receipts__user=request.user
            ).distinct().count()
        except Exception:
            unread_chat_count = 0

    return {
        'current_company': curr_company,
        'current_membership': mem,
        'user_companies': user_companies,
        'user_perms': user_perms,
        'unread_chat_count': unread_chat_count,
    }
