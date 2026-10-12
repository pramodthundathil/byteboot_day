from django.urls import path
from invoicing import views

app_name = 'invoicing'

urlpatterns = [
    path('invoices/', views.invoice_list, name='invoice_list'),
    path('invoices/create/', views.invoice_create, name='invoice_create'),
    path('invoices/<int:invoice_id>/', views.invoice_detail, name='invoice_detail'),
    path('invoices/<int:invoice_id>/post/', views.invoice_post, name='invoice_post'),
    path('invoices/<int:invoice_id>/record-payment/', views.record_invoice_payment, name='record_invoice_payment'),
    path('invoices/<int:invoice_id>/print/', views.invoice_print, name='invoice_print'),
    path('payments/', views.payment_list, name='payment_list'),
    path('payments/create/', views.payment_create, name='payment_create'),
]
