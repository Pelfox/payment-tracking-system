from django.urls import path

from . import views

app_name = "payments"

urlpatterns = [
    path("", views.payment_list, name="list"),
    path("create/", views.payment_create, name="create"),
    path("summary/", views.payment_summary, name="summary"),
    path("partial/", views.partial_payments, name="partial"),
    path("search/", views.payment_search, name="search"),
    path("payers/", views.payer_list, name="payers"),
    path("payers/create/", views.payer_create, name="payer_create"),
    path("invoices/", views.invoice_list, name="invoices"),
    path("invoices/create/", views.invoice_create, name="invoice_create"),
    path("invoices/<slug:slug>/", views.invoice_detail, name="invoice_detail"),
    path("<uuid:payment_id>/", views.payment_detail, name="detail"),
    path("<uuid:payment_id>/edit/", views.payment_update, name="update"),
    path("<uuid:payment_id>/cancel/", views.payment_cancel, name="cancel"),
]
