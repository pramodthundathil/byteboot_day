from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('dashboard.urls')),
    path('', include('tenants.urls')),
    path('crm/', include('crm.urls')),
    path('invoicing/', include('invoicing.urls')),
    path('accounting/', include('accounting.urls')),
    path('hr/', include('hr.urls')),
    path('projects/', include('projects.urls')),
]
