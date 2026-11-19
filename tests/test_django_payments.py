from datetime import datetime
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, OperationalError, connection, transaction
from django.db.models.deletion import ProtectedError
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from payment_tracking_system.models import PaymentMethod, PaymentStatus
from payments.models import Invoice, Payer, Payment


@pytest.fixture
def payments(orm_database):
    ivan = Payer.objects.create(full_name="Иван Иванов")
    anna = Payer.objects.create(full_name="Анна Петрова")
    Payer.objects.create(full_name="Без платежей")
    invoices = [
        Invoice.objects.create(payer=payer, account_number=number, amount_cents=amount)
        for payer, number, amount in (
            (ivan, "ACC-001", 10000),
            (anna, "ACC.[2]", 5000),
            (ivan, "EMPTY", 3000),
        )
    ]
    return [
        Payment.objects.create(
            invoice=invoices[index],
            amount_cents=amount,
            method=method,
            paid_at=datetime.fromisoformat(paid_at),
        )
        for index, amount, method, paid_at in (
            (0, 10000, PaymentMethod.CASH, "2026-10-31T20:59:59+00:00"),
            (0, 6000, PaymentMethod.CARD, "2026-10-31T21:00:00+00:00"),
            (0, 12000, PaymentMethod.CASH, "2026-11-01T20:59:59+00:00"),
            (1, 5000, PaymentMethod.CARD, "2026-11-01T21:00:00+00:00"),
        )
    ]


def test_orm_crud_and_relationship_protection(payments):
    payment = Payment.objects.get(pk=payments[1].pk)
    assert payment.amount == Decimal("60.00")
    assert payment.commission == Decimal("0.60")
    assert payment.remaining_amount == Decimal("40.00")
    assert payment.status_enum is PaymentStatus.PARTIALLY_PAID
    assert payment in payment.invoice.payments.all()
    assert payment.invoice in payment.invoice.payer.invoices.all()
    assert str(payment.invoice.payer) == "Иван Иванов"

    payment.amount_cents = 11000
    payment.save(update_fields=["amount_cents"])
    payment.refresh_from_db()
    assert payment.status_enum is PaymentStatus.OVERPAID
    assert payment.commission_cents == 110
    assert payment.total_charged_cents == 11110
    assert payment.remaining_cents == 0
    assert payment.overpayment_cents == 1000

    with pytest.raises(ProtectedError):
        payment.invoice.delete()
    with pytest.raises(ProtectedError):
        payment.invoice.payer.delete()
    payment.delete()
    assert not Payment.objects.filter(pk=payments[1].pk).exists()
    assert Invoice.objects.filter(pk=payment.invoice.pk).exists()


def test_money_rounding_defaults_and_validation(payments):
    payment = Payment.objects.create(
        invoice=payments[0].invoice, amount_cents=50, method=PaymentMethod.CARD
    )
    assert payment.commission_cents == 1
    assert payment.total_charged == Decimal("0.51")
    assert payment.paid_at.tzinfo is not None
    assert payment.pk != payments[0].pk
    with pytest.raises(ValidationError):
        Payment.objects.create(
            invoice=payment.invoice, amount_cents=0, method=PaymentMethod.CASH
        )
    with pytest.raises(ValidationError):
        Payer(full_name="   ").full_clean()
    with pytest.raises(ValidationError):
        Invoice(
            payer=payment.invoice.payer, account_number="X", amount_cents=0
        ).full_clean()


def test_partial_save_does_not_use_unsaved_input_fields(payments):
    payment = payments[0]
    payment.amount_cents = 50  # Этот атрибут не входит в update_fields.
    payment.method = PaymentMethod.CARD
    payment.save(update_fields=["method"])
    payment.refresh_from_db()
    assert payment.amount_cents == 10000
    assert payment.commission_cents == 100
    assert payment.total_charged_cents == 10100


def test_database_constraints_cannot_be_bypassed(payments):
    with pytest.raises(IntegrityError), transaction.atomic():
        Invoice.objects.create(
            payer=payments[0].invoice.payer,
            account_number="ACC-001",
            amount_cents=10000,
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        Payment.objects.filter(pk=payments[0].pk).update(method="неизвестно")
    with pytest.raises(IntegrityError), transaction.atomic():
        Payment.objects.filter(pk=payments[0].pk).update(total_charged_cents=1)


@pytest.mark.parametrize(
    "route", ["list", "partial", "search", "payers", "invoices", "summary"]
)
def test_existing_pages_and_read_only_methods(client, payments, route):
    url = reverse(f"payments:{route}")
    assert client.get(url).status_code == 200
    assert client.head(url).status_code == 200
    assert client.post(url).status_code == 405


@pytest.mark.parametrize(
    "route", ["list", "partial", "search", "payers", "invoices", "summary"]
)
def test_empty_database_is_supported(client, route):
    assert client.get(reverse(f"payments:{route}")).status_code == 200


def test_home_detail_and_missing_objects(client, payments):
    assert client.get("/").status_code == 200
    with CaptureQueriesContext(connection) as queries:
        response = client.get(reverse("payments:detail", args=[payments[1].pk]))
    assert len(queries) == 2  # Платёж с JOIN и счётчик счетов общей навигации.
    assert response.status_code == 200
    assert "Иван Иванов" in response.content.decode()
    assert "0.60" in response.content.decode()
    assert response.context["payment"].invoice.payer.full_name == "Иван Иванов"
    assert client.get(reverse("payments:detail", args=[uuid4()])).status_code == 404
    assert client.get("/payments/not-a-uuid/").status_code == 404


@pytest.mark.parametrize(
    ("parameters", "indices"),
    [
        ({"status": PaymentStatus.PARTIALLY_PAID.value}, [1]),
        ({"status": PaymentStatus.PAID.value, "method": PaymentMethod.CASH.value}, [0]),
        ({"min_amount": "100.01"}, [2]),
        ({"payer_name": "иван"}, [2, 1, 0]),
        ({"q": "аННа"}, [3]),
        ({"q": "ACC.[2]"}, [3]),
        ({"q": "[.*"}, []),
        ({"q": "' OR 1=1 --"}, []),
        ({"exclude_status": PaymentStatus.PAID.value}, [2, 1]),
        ({"date_from": "2026-11-01", "date_to": "2026-11-01"}, [2, 1]),
        ({"date_from": "2026-11-02"}, [3]),
        ({"date_to": "2026-10-31"}, [0]),
        ({"sort": "amount"}, [3, 1, 0, 2]),
        ({"sort": "amount_desc"}, [2, 0, 1, 3]),
        ({"sort": "date_asc"}, [0, 1, 2, 3]),
        ({"sort": "payer"}, [3, 2, 1, 0]),
        ({"sort": "invoice"}, [2, 1, 0, 3]),
    ],
)
def test_search_filtering_and_sorting(client, payments, parameters, indices):
    response = client.get(reverse("payments:search"), parameters)
    assert response.status_code == 200
    assert list(response.context["payments"]) == [payments[i] for i in indices]


def test_related_filter_partial_list_and_combined_conditions(client, payments):
    response = client.get(
        reverse("payments:search"),
        {
            "payer_id": payments[0].invoice.payer_id,
            "method": "карта",
            "date_from": "2026-11-01",
            "min_amount": "60",
        },
    )
    assert list(response.context["payments"]) == [payments[1]]
    assert list(client.get(reverse("payments:partial")).context["payments"]) == [
        payments[1]
    ]
    response = client.get(
        reverse("payments:search"),
        {"payer_id": Payer.objects.get(full_name="Без платежей").pk},
    )
    assert response.status_code == 200
    assert list(response.context["payments"]) == []
    assert "По заданным условиям платежей нет" in response.content.decode()


@pytest.mark.parametrize(
    "parameters",
    [
        {"status": "unknown"},
        {"method": "unknown"},
        {"exclude_status": "unknown"},
        {"payer_id": "abc"},
        {"payer_id": "99999999"},
        {"invoice_id": "abc"},
        {"invoice_id": "99999999"},
        {"min_amount": "-1"},
        {"min_amount": "0.001"},
        {"min_amount": "NaN"},
        {"date_from": "yesterday"},
        {"date_to": "9999-12-31"},
        {"date_from": "0001-01-01"},
        {"date_from": "2026-11-02", "date_to": "2026-11-01"},
        {"sort": "invoice__payer__password"},
        {"q": "x" * 201},
    ],
)
def test_invalid_parameters_return_form_errors(client, payments, parameters):
    for route in ("list", "search", "partial"):
        response = client.get(reverse(f"payments:{route}"), parameters)
        assert response.status_code == 400
        assert response.context["form"].errors


def test_pagination_preserves_filters_and_sorting(client, payments, monkeypatch):
    monkeypatch.setattr("payments.views.PAGE_SIZE", 1)
    parameters = {"q": "Иван", "sort": "amount", "method": "наличные"}
    response = client.get(reverse("payments:list"), parameters)
    assert list(response.context["payments"]) == [payments[0]]
    assert response.context["page_obj"].paginator.count == 2
    html = response.content.decode()
    assert "sort=amount" in html and "page=2" in html
    assert "method=%D0%BD%D0%B0%D0%BB%D0%B8%D1%87%D0%BD%D1%8B%D0%B5" in html
    assert "q=%D0%98%D0%B2%D0%B0%D0%BD" in html
    parameters["page"] = 2
    response = client.get(reverse("payments:list"), parameters)
    assert list(response.context["payments"]) == [payments[2]]
    assert response.context["page_obj"].has_previous()


@pytest.mark.parametrize(
    ("page", "expected"), [("abc", 1), ("99999", 2), ("-1", 2), ("0", 2)]
)
def test_invalid_page_numbers_are_handled(
    client, payments, monkeypatch, page, expected
):
    monkeypatch.setattr("payments.views.PAGE_SIZE", 2)
    response = client.get(reverse("payments:list"), {"page": page})
    assert response.status_code == 200
    assert response.context["page_obj"].number == expected


def test_list_does_not_issue_queries_per_payment(client, payments):
    with CaptureQueriesContext(connection) as queries:
        response = client.get(reverse("payments:list"))
    assert response.status_code == 200
    # Два списка выбора, COUNT для пагинации, страница с JOIN и общая навигация.
    assert len(queries) == 5


def test_statistics_and_objects_without_payments(client, payments):
    response = client.get(reverse("payments:summary"))
    context = response.context
    assert context["payment_count"] == 4
    assert context["total_amount"] == Decimal(330)
    assert context["total_commission"] == Decimal("1.10")
    assert context["min_amount"] == Decimal(50)
    assert context["max_amount"] == Decimal(120)
    assert context["avg_amount"] == Decimal("82.5")
    assert context["total_remaining"] == Decimal(40)
    assert context["total_overpayment"] == Decimal(20)
    assert context["invoices_without_payments"] == 1
    assert dict(context["status_counts"]) == {
        PaymentStatus.PAID: 2,
        PaymentStatus.PARTIALLY_PAID: 1,
        PaymentStatus.OVERPAID: 1,
    }
    assert {p.full_name: p.payment_count for p in context["payer_statistics"]} == {
        "Иван Иванов": 3,
        "Анна Петрова": 1,
    }
    response = client.get(reverse("payments:summary"), {"min_count": 1})
    assert [p.full_name for p in response.context["payer_statistics"]] == [
        "Иван Иванов"
    ]
    assert (
        client.get(reverse("payments:summary"), {"min_count": "-1"}).status_code == 400
    )
    response = client.get(reverse("payments:payers"))
    empty = next(p for p in response.context["payers"] if p.full_name == "Без платежей")
    assert empty.payment_count == empty.invoice_count == 0
    response = client.get(reverse("payments:invoices"))
    assert {i.account_number for i in response.context["invoices"]} == {
        "ACC-001",
        "ACC.[2]",
        "EMPTY",
    }


def test_database_error_returns_service_unavailable(client):
    with patch(
        "payments.views.Payment.objects.with_related", side_effect=OperationalError
    ):
        response = client.get(reverse("payments:list"))
    assert response.status_code == 503
    assert "Платежи временно недоступны" in response.content.decode()
