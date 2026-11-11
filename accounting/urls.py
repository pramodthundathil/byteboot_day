from django.urls import path
from accounting import views

app_name = 'accounting'

urlpatterns = [
    # Chart of Accounts
    path('chart-of-accounts/', views.chart_of_accounts, name='chart_of_accounts'),
    path('chart-of-accounts/create/', views.account_create, name='account_create'),
    path('chart-of-accounts/<int:account_id>/edit/', views.account_edit, name='account_edit'),
    path('chart-of-accounts/<int:account_id>/', views.account_detail, name='account_detail'),
    path('chart-of-accounts/export/', views.account_export_csv, name='account_export_csv'),
    path('chart-of-accounts/bulk-upload/', views.account_bulk_upload, name='account_bulk_upload'),

    # General Ledger & Account Statements
    path('general-ledger/', views.general_ledger, name='general_ledger'),

    # Manual Journals
    path('journals/', views.journal_list, name='journal_list'),
    path('journals/create/', views.journal_create, name='journal_create'),
    path('journals/<int:journal_id>/', views.journal_detail, name='journal_detail'),
    path('journals/<int:journal_id>/reverse/', views.journal_reverse, name='journal_reverse'),

    # Vouchers (Sales, Purchase, Receipt, Payment, Journal, Contra)
    path('vouchers/', views.voucher_list, name='voucher_list'),
    path('vouchers/create/', views.voucher_create, name='voucher_create'),
    path('vouchers/<int:voucher_id>/', views.voucher_detail, name='voucher_detail'),
    path('vouchers/<int:voucher_id>/cancel/', views.voucher_cancel, name='voucher_cancel'),

    # Banking & Cash Accounts
    path('banking/', views.banking_overview, name='banking_overview'),
    path('banking/accounts/create/', views.bank_account_create, name='bank_account_create'),
    path('banking/transfer/', views.bank_transfer_contra, name='bank_transfer_contra'),
    path('banking/accounts/<int:account_id>/', views.bank_account_detail, name='bank_account_detail'),

    # Fixed Assets & Depreciation
    path('fixed-assets/', views.fixed_assets_list, name='fixed_assets_list'),
    path('fixed-assets/create/', views.fixed_asset_create, name='fixed_asset_create'),
    path('fixed-assets/<int:asset_id>/', views.fixed_asset_detail, name='fixed_asset_detail'),
    path('fixed-assets/<int:asset_id>/post-depreciation/', views.fixed_asset_post_depreciation, name='fixed_asset_post_depreciation'),

    # Taxes
    path('taxes/', views.tax_management, name='tax_management'),
    path('taxes/create/', views.tax_create, name='tax_create'),
    path('taxes/<int:tax_id>/toggle/', views.tax_toggle, name='tax_toggle'),

    # Financial Reports & Statements
    path('reports/', views.financial_reports, name='financial_reports'),
    path('reports/export/<str:report_type>/', views.financial_reports_export, name='financial_reports_export'),
]
