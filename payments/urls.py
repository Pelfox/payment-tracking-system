from django.urls import path

from . import views

app_name = "payments"

urlpatterns = [
    path("", views.payment_list, name="list"),
    path("summary/", views.payment_summary, name="summary"),
    path("<uuid:payment_id>/", views.payment_detail, name="detail"),
]
