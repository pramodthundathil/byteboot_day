from django import template

register = template.Library()

@register.filter(name='has_perm')
def has_perm(request_or_user, perm_str):
    """
    Template filter to check granular permissions cleanly in Django templates.
    Usage:
        {% load permissions %}
        {% if request|has_perm:'projects.create' %}
            <a href="...">+ Create Task</a>
        {% endif %}
        {% if request|has_perm:'hr.approve' %}
            <button>Approve Leave</button>
        {% endif %}
    """
    if not request_or_user or not perm_str:
        return False

    membership = getattr(request_or_user, 'membership', None)
    if not membership and hasattr(request_or_user, 'user'):
        membership = getattr(request_or_user.user, 'membership', None)

    if not membership:
        return False

    parts = str(perm_str).split('.')
    if len(parts) == 2:
        module, action = parts[0], parts[1]
        return membership.has_permission(module, action)
    elif len(parts) == 1:
        return membership.has_permission(parts[0], 'view')
    return False
