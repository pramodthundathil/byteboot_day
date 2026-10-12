from django.urls import path
from accounting import views

app_name = 'accounting'

urlpatterns = [
    path('journals/', views.journal_list, name='journal_list'),
    path('journals/create/', views.journal_create, name='journal_create'),
    path('journals/<int:journal_id>/', views.journal_detail, name='journal_detail'),
    path('journals/<int:journal_id>/reverse/', views.journal_reverse, name='journal_reverse'),
    path('chart-of-accounts/', views.chart_of_accounts, name='chart_of_accounts'),
    path('chart-of-accounts/create/', views.account_create, name='account_create'),
    path('chart-of-accounts/<int:account_id>/edit/', views.account_edit, name='account_edit'),
    path('reports/', views.financial_reports, name='financial_reports'),
]
