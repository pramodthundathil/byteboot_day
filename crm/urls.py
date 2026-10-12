from django.urls import path
from crm import views

app_name = 'crm'

urlpatterns = [
    path('leads/', views.lead_list, name='lead_list'),
    path('leads/create/', views.lead_create, name='lead_create'),
    path('leads/<int:lead_id>/', views.lead_detail, name='lead_detail'),
    path('leads/<int:lead_id>/ajax-detail/', views.lead_ajax_detail, name='lead_ajax_detail'),
    path('leads/<int:lead_id>/convert/', views.lead_convert, name='lead_convert'),
    path('customers/', views.customer_list, name='customer_list'),
    path('customers/<int:customer_id>/', views.customer_detail, name='customer_detail'),
]
