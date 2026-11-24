from django.urls import path

from . import views

app_name = "payments"

urlpatterns = [
    path("", views.PaymentListView.as_view(), name="list"),
    path("create/", views.PaymentCreateView.as_view(), name="create"),
    path("summary/", views.payment_summary, name="summary"),
    path("partial/", views.PartialPaymentListView.as_view(), name="partial"),
    path("search/", views.PaymentSearchView.as_view(), name="search"),
    path("payers/", views.payer_list, name="payers"),
    path("payers/create/", views.payer_create, name="payer_create"),
    path("invoices/", views.invoice_list, name="invoices"),
    path("invoices/create/", views.invoice_create, name="invoice_create"),
    path("invoices/<slug:slug>/", views.invoice_detail, name="invoice_detail"),
    path("<uuid:payment_id>/", views.PaymentDetailView.as_view(), name="detail"),
    path("<uuid:payment_id>/edit/", views.PaymentUpdateView.as_view(), name="update"),
    path("<uuid:payment_id>/cancel/", views.payment_cancel, name="cancel"),
]
