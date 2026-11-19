from datetime import date, datetime
from io import StringIO
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.db import OperationalError, connection
from django.db.migrations.executor import MigrationExecutor
from django.db.models import F, Prefetch
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from payments.models import Invoice, Payer, Payment


@pytest.fixture
def records(orm_database):
    payer = Payer.objects.create(full_name="Анна Петрова")
    invoice = Invoice.objects.create(
        payer=payer, account_number="СЧЁТ.[1]", amount_cents=10000
    )
    return [
        Payment.objects.create(
            invoice=invoice,
            amount_cents=amount,
            method=method,
            paid_at=datetime.fromisoformat(timestamp),
        )
        for amount, method, timestamp in (
            (10000, Payment.Method.CASH, "2026-10-31T20:59:59+00:00"),
            (6000, Payment.Method.CARD, "2026-10-31T21:00:00+00:00"),
            (12000, Payment.Method.CARD, "2026-11-01T20:59:59+00:00"),
        )
    ]


@pytest.fixture
def admin_client(client):
    user = get_user_model().objects.create_superuser(
        username="test-admin", email="admin@example.com", password="test-password"
    )
    client.force_login(user)
    return client


def test_queryset_statuses_dates_projection_and_reverse_relations(records):
    assert list(Payment.objects.paid()) == [records[0]]
    assert list(Payment.objects.partially_paid()) == [records[1]]
    assert list(Payment.objects.overpaid()) == [records[2]]
    assert list(Payment.objects.for_date(date(2026, 11, 1))) == records[:0:-1]
    assert list(
        Payment.objects.partially_paid().for_date(date(2026, 11, 1)).search("аННа")
    ) == [records[1]]
    assert Payment.objects.search("[.*").count() == 0
    with CaptureQueriesContext(connection) as queries:
        rows = list(
            Payment.objects.with_labels().values("payer_name", "account_number")
        )
    assert len(queries) == 1
    assert {row["payer_name"] for row in rows} == {"Анна Петрова"}
    assert {row["account_number"] for row in rows} == {"СЧЁТ.[1]"}
    with CaptureQueriesContext(connection) as queries:
        payers = list(
            Payer.objects.prefetch_related(
                Prefetch("invoices__payments", queryset=Payment.objects.paid())
            )
        )
        assert list(payers[0].invoices.all()[0].payments.all()) == [records[0]]
    assert len(queries) == 3


def test_generated_field_is_updated_by_database(records):
    payment = records[1]
    assert payment.has_commission is True
    Payment.objects.filter(pk=payment.pk).update(
        commission_cents=0, total_charged_cents=F("amount_cents")
    )
    payment.refresh_from_db()
    assert payment.has_commission is False
    payment.save()
    payment.refresh_from_db()
    assert payment.has_commission is True
    assert payment.commission_cents == 60


def test_invoice_slug_is_ascii_unique_stable_and_used_in_pages(client, records):
    invoice = records[0].invoice
    assert invoice.slug.isascii()
    assert invoice.slug.startswith("schiot1-")
    other = Invoice.objects.create(
        payer=invoice.payer, account_number=invoice.account_number, amount_cents=20000
    )
    assert invoice.slug != other.slug
    original_slug = other.slug
    other.account_number = "Другой номер"
    other.save(update_fields=["account_number"])
    other.refresh_from_db()
    assert other.slug == original_slug

    response = client.get(invoice.get_absolute_url())
    assert response.status_code == 200
    assert list(response.context["payments"]) == records[::-1]
    assert records[0].get_absolute_url() in response.content.decode()
    assert (
        invoice.get_absolute_url()
        in client.get(reverse("payments:invoices")).content.decode()
    )
    assert client.get("/payments/invoices/missing/").status_code == 404
    assert client.post(invoice.get_absolute_url()).status_code == 405
    response = client.get(other.get_absolute_url())
    assert "По этому счёту платежей пока нет" in response.content.decode()


def test_invoice_filter_and_context_on_multiple_pages(client, records):
    invoice = records[0].invoice
    response = client.get(reverse("payments:search"), {"invoice_id": invoice.pk})
    assert list(response.context["payments"]) == records[::-1]
    for url in ("/", reverse("payments:list"), invoice.get_absolute_url()):
        response = client.get(url)
        assert response.context["site_name"] == "Учёт платежей"
        assert response.context["navigation_invoice_count"] == 1
        assert "Счета (1)" in response.content.decode()
    Invoice.objects.create(payer=invoice.payer, account_number="NEW", amount_cents=100)
    assert "Счета (2)" in client.get("/").content.decode()


def test_context_processor_does_not_break_database_error_page(client):
    with (
        patch(
            "payments.views.Payment.objects.with_related", side_effect=OperationalError
        ),
        patch(
            "payments.context_processors.Invoice.objects.count",
            side_effect=OperationalError,
        ),
    ):
        response = client.get(reverse("payments:list"))
    assert response.status_code == 503
    assert "Учёт платежей" in response.content.decode()


@pytest.mark.parametrize("model", ["payer", "invoice", "payment"])
def test_admin_requires_staff_and_model_permission(client, model):
    url = reverse(f"admin:payments_{model}_changelist")
    assert client.get(url).status_code == 302
    user = get_user_model().objects.create_user(username="visitor", password="password")
    client.force_login(user)
    assert client.get(url).status_code == 302
    user.is_staff = True
    user.save(update_fields=["is_staff"])
    assert client.get(url).status_code == 403


@pytest.mark.parametrize("model", ["payer", "invoice", "payment"])
def test_admin_lists_search_and_forms(admin_client, records, model):
    url = reverse(f"admin:payments_{model}_changelist")
    response = admin_client.get(url, {"q": "аННа"})
    assert response.status_code == 200
    assert response.context["cl"].result_count == (3 if model == "payment" else 1)
    assert admin_client.get(url, {"q": "missing"}).context["cl"].result_count == 0
    assert admin_client.get(reverse(f"admin:payments_{model}_add")).status_code == 200


def test_admin_filters_and_pagination(admin_client, records, monkeypatch):
    from django.contrib import admin

    monkeypatch.setattr(admin.site._registry[Payment], "list_per_page", 1)
    url = reverse("admin:payments_payment_changelist")
    response = admin_client.get(url, {"method__exact": "карта", "p": 2})
    assert response.status_code == 200
    assert response.context["cl"].result_count == 2
    assert list(response.context["cl"].result_list) == [records[1]]
    for parameters in (
        {"status__exact": Payment.Status.PARTIALLY_PAID},
        {"has_commission__exact": "0"},
        {"q": str(records[1].pk)},
    ):
        response = admin_client.get(url, parameters)
        assert response.context["cl"].result_count == 1


def test_admin_payment_crud_recalculates_and_protects_relationships(
    admin_client, records
):
    invoice = records[0].invoice
    payload = {
        "invoice": invoice.pk,
        "amount_cents": 5000,
        "method": Payment.Method.CARD,
        "paid_at_0": "2026-11-09",
        "paid_at_1": "12:30:00",
        "_save": "Сохранить",
    }
    response = admin_client.post(reverse("admin:payments_payment_add"), payload)
    assert response.status_code == 302
    payment = Payment.objects.exclude(pk__in=[row.pk for row in records]).get()
    assert payment.commission_cents == 50
    assert payment.status == Payment.Status.PARTIALLY_PAID
    change_url = reverse("admin:payments_payment_change", args=[payment.pk])
    payload.update(amount_cents=11000, status=Payment.Status.PAID)
    assert admin_client.post(change_url, payload).status_code == 302
    payment.refresh_from_db()
    assert payment.status == Payment.Status.OVERPAID
    assert payment.total_charged_cents == 11110
    assert payment.has_commission is True
    payload["amount_cents"] = 0
    response = admin_client.post(change_url, payload)
    assert response.status_code == 200
    assert response.context["adminform"].form.errors
    payment.refresh_from_db()
    assert payment.amount_cents == 11000
    response = admin_client.get(
        reverse("admin:payments_invoice_delete", args=[invoice.pk])
    )
    assert response.context["protected"]
    assert (
        admin_client.post(
            reverse("admin:payments_payment_delete", args=[payment.pk]), {"post": "yes"}
        ).status_code
        == 302
    )
    assert not Payment.objects.filter(pk=payment.pk).exists()


def test_admin_inlines_and_invoice_snapshots_are_read_only(admin_client, records):
    invoice = records[0].invoice
    for model, obj in (("invoice", invoice), ("payer", invoice.payer)):
        response = admin_client.get(
            reverse(f"admin:payments_{model}_change", args=[obj.pk])
        )
        assert response.status_code == 200
        inline = response.context["inline_admin_formsets"][0]
        assert inline.has_add_permission is False
        assert inline.has_change_permission is False
        assert inline.has_delete_permission is False
    response = admin_client.get(
        reverse("admin:payments_invoice_change", args=[invoice.pk])
    )
    assert "amount_cents" not in response.context["adminform"].form.fields
    assert "account_number" not in response.context["adminform"].form.fields
    assert response.context["has_absolute_url"]
    assert (
        admin_client.get(response.context["absolute_url"], follow=True).status_code
        == 200
    )
    response = admin_client.get(
        reverse("admin:payments_payer_change", args=[invoice.payer_id])
    )
    assert str(records[0].pk) in response.content.decode()


def test_view_only_staff_cannot_change_payments(client, records):
    user = get_user_model().objects.create_user(username="reader", is_staff=True)
    user.user_permissions.add(Permission.objects.get(codename="view_payment"))
    client.force_login(user)
    url = reverse("admin:payments_payment_change", args=[records[0].pk])
    assert client.get(url).status_code == 200
    assert client.post(url, {"amount_cents": 1}).status_code == 403
    assert client.get(reverse("admin:payments_payment_add")).status_code == 403


def test_admin_action_uses_business_calculation(admin_client, records):
    Payment.objects.filter(pk=records[1].pk).update(
        commission_cents=0, total_charged_cents=F("amount_cents")
    )
    response = admin_client.post(
        reverse("admin:payments_payment_changelist"),
        {
            "action": "recalculate_payments",
            "_selected_action": [records[1].pk],
        },
        follow=True,
    )
    assert response.status_code == 200
    records[1].refresh_from_db()
    assert records[1].commission_cents == 60
    assert "Пересчитано платежей: 1" in response.content.decode()


def test_related_queries_do_not_grow_with_number_of_payments(client, records):
    url = reverse("payments:list")
    with CaptureQueriesContext(connection) as before:
        assert client.get(url).status_code == 200
    for _ in range(5):
        Payment.objects.create(
            invoice=records[0].invoice, amount_cents=100, method="карта"
        )
    with CaptureQueriesContext(connection) as after:
        assert client.get(url).status_code == 200
    assert len(before) == len(after) == 5


def test_orm_inspection_command_is_read_only(records):
    output = StringIO()
    call_command("inspect_payment_orm", stdout=output)
    assert "Без select_related: 7 SQL-запросов" in output.getvalue()
    assert "С select_related: 1 SQL-запросов" in output.getvalue()
    assert "HAVING" in output.getvalue()
    assert Payment.objects.count() == 3


def test_migration_preserves_existing_payments_and_populates_unique_slugs(
    django_database,
):
    old_target = [("payments", "0001_initial")]
    new_target = [("payments", "0002_admin_orm_features")]
    executor = MigrationExecutor(connection)
    latest_targets = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate(old_target)
        old_apps = executor.loader.project_state(old_target).apps
        payer = old_apps.get_model("payments", "Payer").objects.create(
            full_name="Миграция"
        )
        invoices = [
            old_apps.get_model("payments", "Invoice").objects.create(
                payer=payer,
                account_number="Счёт №1",
                amount_cents=amount,
            )
            for amount in (10000, 20000)
        ]
        old_payment = old_apps.get_model("payments", "Payment").objects.create(
            invoice=invoices[0],
            amount_cents=6000,
            method="карта",
            paid_at=datetime.fromisoformat("2026-11-01T12:00:00+03:00"),
            status="частичная оплата",
            commission_cents=60,
            total_charged_cents=6060,
            remaining_cents=4000,
            overpayment_cents=0,
        )
        MigrationExecutor(connection).migrate(new_target)
        migrated_apps = (
            MigrationExecutor(connection).loader.project_state(new_target).apps
        )
        migrated = migrated_apps.get_model("payments", "Payment").objects.get(
            pk=old_payment.pk
        )
        assert migrated.invoice_id == invoices[0].pk
        assert migrated.invoice.payer_id == payer.pk
        assert migrated.status == "частичная оплата"
        assert migrated.total_charged_cents == 6060
        assert migrated.has_commission is True
        slugs = list(
            migrated_apps.get_model("payments", "Invoice").objects.values_list(
                "slug", flat=True
            )
        )
        assert len(set(slugs)) == 2
        assert all(slug and slug.isascii() for slug in slugs)
    finally:
        MigrationExecutor(connection).migrate(latest_targets)
        Payment.objects.all().delete()
        Invoice.objects.all().delete()
        Payer.objects.all().delete()
