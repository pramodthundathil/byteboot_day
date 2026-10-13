from django.urls import path
from crm import views

app_name = 'crm'

urlpatterns = [
    path('', views.followup_dashboard, name='dashboard'),
    path('followups/', views.followup_dashboard, name='followup_dashboard'),
    path('leads/', views.lead_list, name='lead_list'),
    path('leads/create/', views.lead_create, name='lead_create'),
    path('leads/<int:lead_id>/', views.lead_detail, name='lead_detail'),
    path('leads/<int:lead_id>/edit/', views.lead_edit, name='lead_edit'),
    path('leads/<int:lead_id>/delete/', views.lead_delete, name='lead_delete'),
    path('leads/<int:lead_id>/quick-followup/', views.lead_quick_followup, name='lead_quick_followup'),
    path('leads/<int:lead_id>/quick-assign/', views.lead_quick_assign, name='lead_quick_assign'),
    path('leads/<int:lead_id>/ajax-detail/', views.lead_ajax_detail, name='lead_ajax_detail'),
    path('leads/<int:lead_id>/convert/', views.lead_convert, name='lead_convert'),
    path('leads/bulk-action/', views.lead_bulk_action, name='lead_bulk_action'),
    path('leads/bulk-upload/', views.lead_bulk_upload, name='lead_bulk_upload'),
    path('leads/template-download/', views.lead_template_download, name='lead_template_download'),
    path('leads/template-download/excel/', views.lead_template_excel_download, name='lead_template_excel_download'),
    path('analytics/', views.staff_analytics, name='staff_analytics'),
    path('analytics/export/', views.staff_analytics_export, name='staff_analytics_export'),
    path('customers/', views.customer_list, name='customer_list'),
    path('customers/<int:customer_id>/', views.customer_detail, name='customer_detail'),
]
