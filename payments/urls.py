from django.urls import path

from . import views

app_name = "payments"

urlpatterns = [
    path("", views.payment_list, name="list"),
    path("summary/", views.payment_summary, name="summary"),
    path("partial/", views.partial_payments, name="partial"),
    path("search/", views.payment_search, name="search"),
    path("payers/", views.payer_list, name="payers"),
    path("invoices/", views.invoice_list, name="invoices"),
    path("<uuid:payment_id>/", views.payment_detail, name="detail"),
]
