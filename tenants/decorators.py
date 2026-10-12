from functools import wraps
from django.contrib import messages
from django.shortcuts import redirect

def require_permission(module, action='view'):
    """
    Decorator for views to strictly enforce multi-tenant module and action authorization.
    Superusers bypass permission checks automatically.
    Redirects unauthorized users to the Dashboard with an explicit error toast message.
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('account_login')

            if request.user.is_superuser:
                return view_func(request, *args, **kwargs)

            membership = getattr(request, 'membership', None)
            if not membership or not membership.has_permission(module, action):
                messages.error(
                    request, 
                    f"Access Denied: You do not have '{action}' authorization for the {module.upper()} module."
                )
                return redirect('dashboard:index')

            return view_func(request, *args, **kwargs)
        return _wrapped_view
    return decorator
