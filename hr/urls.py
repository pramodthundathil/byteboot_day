from django.urls import path
from hr import views

app_name = 'hr'

urlpatterns = [
    path('employees/', views.employee_list, name='employee_list'),
    path('employees/create/', views.employee_create, name='employee_create'),
    path('employees/<int:employee_id>/', views.employee_detail, name='employee_detail'),
    path('employees/<int:employee_id>/edit/', views.employee_edit, name='employee_edit'),
    path('employees/<int:employee_id>/ajax-detail/', views.employee_ajax_detail, name='employee_ajax_detail'),
    path('leaves/', views.leave_list, name='leave_list'),
    path('leaves/request/', views.leave_request, name='leave_request'),
    path('leaves/<int:leave_id>/edit/', views.leave_edit, name='leave_edit'),
    path('leaves/<int:leave_id>/<str:action>/', views.leave_action, name='leave_action'),
]
