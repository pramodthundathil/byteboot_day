from django.urls import path
from projects import views

app_name = 'projects'

urlpatterns = [
    path('projects/', views.project_list, name='project_list'),
    path('projects/create/', views.project_create, name='project_create'),
    path('projects/<int:project_id>/', views.project_detail, name='project_detail'),
    path('projects/<int:project_id>/edit/', views.project_edit, name='project_edit'),
    path('tasks/', views.task_list, name='task_list'),
    path('tasks/create/', views.task_create, name='task_create'),
    path('tasks/<int:task_id>/', views.task_detail, name='task_detail'),
    path('tasks/<int:task_id>/edit/', views.task_edit, name='task_edit'),
    path('tasks/lookup/<str:task_number>/', views.task_lookup, name='task_lookup'),
    path('invoices/lookup/<str:invoice_number>/', views.invoice_lookup, name='invoice_lookup'),
    path('leads/lookup/<str:lead_number>/', views.lead_lookup, name='lead_lookup'),
    path('api/entities/search/', views.api_entity_search, name='api_entity_search'),
    path('api/chat/unread-summary/', views.api_chat_unread_summary, name='api_chat_unread_summary'),
    path('api/chat/thread/<int:project_id>/', views.api_chat_thread, name='api_chat_thread'),
    path('api/chat/mark-read/<int:project_id>/', views.api_chat_mark_read, name='api_chat_mark_read'),
    path('api/chat/send/<int:project_id>/', views.api_chat_send, name='api_chat_send'),
    path('conversations/', views.conversations, name='conversations'),
]

