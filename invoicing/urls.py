from django.urls import path
from invoicing import views

app_name = 'invoicing'

urlpatterns = [
    # Invoices (Sales)
    path('invoices/', views.invoice_list, name='invoice_list'),
    path('invoices/create/', views.invoice_create, name='invoice_create'),
    path('invoices/<int:invoice_id>/', views.invoice_detail, name='invoice_detail'),
    path('invoices/<int:invoice_id>/post/', views.invoice_post, name='invoice_post'),
    path('invoices/<int:invoice_id>/record-payment/', views.record_invoice_payment, name='record_invoice_payment'),
    path('invoices/<int:invoice_id>/print/', views.invoice_print, name='invoice_print'),

    # Customer Payments (AR) & Quick Create
    path('payments/', views.payment_list, name='payment_list'),
    path('payments/create/', views.payment_create, name='payment_create'),
    path('customers/quick-create/', views.customer_quick_create, name='customer_quick_create'),

    # Products & Services Catalog
    path('products/', views.product_list, name='product_list'),
    path('products/create/', views.product_create, name='product_create'),
    path('products/quick-create/', views.product_quick_create, name='product_quick_create'),

    # Vendors (Suppliers / AP)
    path('vendors/', views.vendor_list, name='vendor_list'),
    path('vendors/create/', views.vendor_create, name='vendor_create'),
    path('vendors/<int:vendor_id>/', views.vendor_detail, name='vendor_detail'),

    # Vendor Bills (Purchases)
    path('bills/', views.bill_list, name='bill_list'),
    path('bills/create/', views.bill_create, name='bill_create'),
    path('bills/<int:bill_id>/', views.bill_detail, name='bill_detail'),
    path('bills/<int:bill_id>/post/', views.bill_post, name='bill_post'),
    path('bills/<int:bill_id>/record-payment/', views.record_bill_payment, name='record_bill_payment'),

    # Payments Made (Disbursements)
    path('payments-made/', views.payment_made_list, name='payment_made_list'),
    path('payments-made/create/', views.payment_made_create, name='payment_made_create'),
    path('payments-made/<int:payment_id>/', views.payment_made_detail, name='payment_made_detail'),
]
