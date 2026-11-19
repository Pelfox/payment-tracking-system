from datetime import timedelta
from decimal import Decimal
from io import BytesIO
from uuid import uuid4

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from payments.forms import PaymentForm
from payments.models import Invoice, Payer, Payment


@pytest.fixture
def invoice(orm_database):
    payer = Payer.objects.create(full_name="Плательщик формы")
    return Invoice.objects.create(
        payer=payer, account_number="FORM-001", amount_cents=10000
    )


@pytest.fixture
def payload(invoice):
    return {
        "payer": invoice.payer_id,
        "invoice": invoice.pk,
        "amount_cents": "6000",
        "method": Payment.Method.CARD,
        "paid_at": "2026-01-10T12:30:15",
    }


def image_file(name="receipt.png", *, image_format="PNG"):
    stream = BytesIO()
    Image.new("RGB", (4, 4), "white").save(stream, format=image_format)
    return SimpleUploadedFile(name, stream.getvalue(), content_type="image/png")


def test_create_payment_uses_model_calculation_and_redirect(client, payload, invoice):
    url = reverse("payments:create")
    assert client.get(url).status_code == 200
    response = client.post(
        url,
        {
            **payload,
            "status": Payment.Status.PAID,
            "commission_cents": 0,
            "cancelled_at": "2026-01-01",
        },
    )
    payment = Payment.objects.get()
    assert response.status_code == 302
    assert response.url == payment.get_absolute_url()
    assert payment.invoice == invoice
    assert payment.status == Payment.Status.PARTIALLY_PAID
    assert payment.commission_cents == 60
    assert payment.remaining_cents == 4000
    assert payment.total_charged_cents == 6060
    assert payment.has_commission and not payment.is_cancelled
    assert (
        timezone.localtime(payment.paid_at).isoformat() == "2026-01-10T12:30:15+03:00"
    )
    assert client.get(response.url).status_code == 200
    assert Payment.objects.count() == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("amount_cents", "0"),
        ("amount_cents", "-1"),
        ("amount_cents", "1.5"),
        ("amount_cents", "1000000000000"),
        ("amount_cents", "не число"),
        ("amount_cents", ""),
        ("method", "перевод"),
        ("payer", "999999"),
        ("invoice", "999999"),
        ("paid_at", "2026-02-30T12:00"),
        ("paid_at", ""),
        ("paid_at", "9999-12-31T23:59"),
    ],
)
def test_invalid_post_preserves_values_without_saving(client, payload, field, value):
    payload[field] = value
    response = client.post(reverse("payments:create"), payload)
    assert response.status_code == 400
    assert field in response.context["form"].errors
    assert response.context["form"]["amount_cents"].value() == payload["amount_cents"]
    assert Payment.objects.count() == 0


def test_empty_post_is_bound_and_shows_errors(client):
    response = client.post(reverse("payments:create"), {})
    assert response.status_code == 400
    assert response.context["form"].is_bound
    assert response.context["form"].errors


def test_payment_date_allows_history_and_rejects_future(payload):
    form = PaymentForm(payload)
    assert form.is_valid(), form.errors
    payload["paid_at"] = timezone.localtime(
        timezone.now() + timedelta(days=1)
    ).isoformat()
    form = PaymentForm(payload)
    assert not form.is_valid()
    assert "paid_at" in form.errors


def test_invoice_must_belong_to_selected_payer(client, payload):
    payload["payer"] = Payer.objects.create(full_name="Другой плательщик").pk
    response = client.post(reverse("payments:create"), payload)
    assert response.status_code == 400
    assert "другому плательщику" in response.context["form"].non_field_errors()[0]
    assert not Payment.objects.exists()


def test_update_prefills_instance_and_recalculates_without_duplicate(
    client, invoice, payload
):
    payment = Payment.objects.create(
        invoice=invoice, amount_cents=6000, method=Payment.Method.CARD
    )
    url = reverse("payments:update", args=[payment.pk])
    response = client.get(url)
    assert response.context["form"].instance.pk == payment.pk
    assert response.context["form"]["payer"].value() == invoice.payer_id
    assert response.context["form"]["amount_cents"].value() == 6000
    assert client.post(url, payload).status_code == 302
    assert Payment.objects.count() == 1
    payload.update(amount_cents=12000, method=Payment.Method.CASH)
    assert client.post(url, payload).status_code == 302
    payment.refresh_from_db()
    assert payment.status == Payment.Status.OVERPAID
    assert payment.overpayment_cents == 2000
    assert payment.commission_cents == 0
    assert not payment.has_commission
    payload["amount_cents"] = 0
    assert client.post(url, payload).status_code == 400
    payment.refresh_from_db()
    assert payment.amount_cents == 12000


def test_cancel_keeps_history_and_excludes_from_lists_and_totals(
    client, invoice, payload
):
    payment = Payment.objects.create(
        invoice=invoice, amount_cents=6000, method=Payment.Method.CARD
    )
    url = reverse("payments:cancel", args=[payment.pk])
    assert client.get(url).status_code == 405
    payment.refresh_from_db()
    assert not payment.is_cancelled
    response = client.post(url)
    assert response.status_code == 302
    payment.refresh_from_db()
    cancelled_at = payment.cancelled_at
    assert payment.is_cancelled
    assert payment.status == Payment.Status.PARTIALLY_PAID
    assert payment.commission_cents == 60
    assert Payment.objects.count() == 1
    assert not Payment.objects.active().exists()
    assert list(Payment.objects.cancelled()) == [payment]
    assert not payment.cancel()
    assert client.post(url).status_code == 404
    payment.refresh_from_db()
    assert payment.cancelled_at == cancelled_at
    for name in ("list", "partial"):
        assert not list(client.get(reverse(f"payments:{name}")).context["payments"])
    for lifecycle in ("all", "cancelled"):
        response = client.get(reverse("payments:list"), {"lifecycle": lifecycle})
        assert list(response.context["payments"]) == [payment]
        assert "Отменён" in response.content.decode()
    summary = client.get(reverse("payments:summary")).context
    assert summary["payment_count"] == 0
    assert summary["total_amount"] == Decimal(0)
    assert summary["total_commission"] == Decimal(0)
    assert summary["total_remaining"] == Decimal(0)
    assert summary["payer_statistics"] == []
    assert summary["method_totals"] == []
    assert (
        client.get(reverse("payments:payers")).context["payers"][0].payment_count == 0
    )
    assert "Запись отменена" in client.get(payment.get_absolute_url()).content.decode()
    assert list(client.get(invoice.get_absolute_url()).context["payments"]) == [payment]
    edit_url = reverse("payments:update", args=[payment.pk])
    assert client.get(edit_url).status_code == 404
    assert client.post(edit_url, payload).status_code == 404
    assert client.post(reverse("payments:create"), payload).status_code == 302


@pytest.mark.parametrize("route", ["update", "cancel"])
def test_missing_payment_is_404(client, route):
    url = reverse(f"payments:{route}", args=[uuid4()])
    assert client.post(url, {}).status_code == 404
    if route == "update":
        assert client.get(url).status_code == 404


def test_related_objects_can_be_created_without_admin(client):
    assert client.get(reverse("payments:payer_create")).status_code == 200
    response = client.post(
        reverse("payments:payer_create"), {"full_name": "  Анна   Петрова  "}
    )
    assert response.status_code == 302
    payer = Payer.objects.get(full_name="Анна Петрова")
    assert (
        client.post(
            reverse("payments:payer_create"), {"full_name": payer.full_name}
        ).status_code
        == 400
    )
    assert (
        client.post(reverse("payments:payer_create"), {"full_name": "  "}).status_code
        == 400
    )
    assert client.get(reverse("payments:invoice_create")).status_code == 200
    payload = {"payer": payer.pk, "account_number": "НОВЫЙ-1", "amount_cents": 10000}
    response = client.post(reverse("payments:invoice_create"), payload)
    assert response.status_code == 302
    invoice = Invoice.objects.get()
    assert response.url == invoice.get_absolute_url()
    assert invoice.slug and invoice.payer == payer
    assert client.post(reverse("payments:invoice_create"), payload).status_code == 400
    assert Invoice.objects.count() == 1


def test_receipt_upload_replace_clear_and_preserve_on_errors(client, payload, tmp_path):
    with override_settings(MEDIA_ROOT=tmp_path):
        response = client.post(
            reverse("payments:create"), {**payload, "receipt": image_file()}
        )
        assert response.status_code == 302
        payment = Payment.objects.get()
        original = payment.receipt.name
        assert (tmp_path / original).is_file()
        assert (
            payment.receipt.url
            in client.get(payment.get_absolute_url()).content.decode()
        )
        url = reverse("payments:update", args=[payment.pk])
        assert (
            client.post(
                url, {**payload, "amount_cents": 0, "receipt": image_file("bad.png")}
            ).status_code
            == 400
        )
        payment.refresh_from_db()
        assert payment.receipt.name == original
        assert not list(tmp_path.rglob("bad.png"))
        assert client.post(url, payload).status_code == 302
        payment.refresh_from_db()
        assert payment.receipt.name == original
        assert (
            client.post(
                url, {**payload, "receipt": image_file("replacement.png")}
            ).status_code
            == 302
        )
        payment.refresh_from_db()
        assert payment.receipt.name != original
        assert (tmp_path / payment.receipt.name).is_file()
        assert client.post(url, {**payload, "receipt-clear": "on"}).status_code == 302
        payment.refresh_from_db()
        assert not payment.receipt


@pytest.mark.parametrize("kind", ["fake", "unsupported", "extension", "oversized"])
def test_invalid_images_are_rejected_without_writing(client, payload, tmp_path, kind):
    if kind == "fake":
        upload = SimpleUploadedFile(
            "fake.png", b"not an image", content_type="image/png"
        )
    elif kind == "unsupported":
        upload = image_file("receipt.png", image_format="GIF")
    elif kind == "extension":
        upload = image_file("receipt.html")
    else:
        upload = image_file()
        upload = SimpleUploadedFile(
            "large.png", upload.read() + b"0" * (5 * 1024 * 1024)
        )
    with override_settings(MEDIA_ROOT=tmp_path):
        response = client.post(
            reverse("payments:create"), {**payload, "receipt": upload}
        )
    assert response.status_code == 400
    assert "receipt" in response.context["form"].errors
    assert not Payment.objects.exists()
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "action", ["create", "update", "cancel", "payer_create", "invoice_create"]
)
def test_mutations_require_csrf(invoice, payload, action):
    client = Client(enforce_csrf_checks=True)
    payment = Payment.objects.create(
        invoice=invoice, amount_cents=6000, method=Payment.Method.CARD
    )
    args = [payment.pk] if action in {"update", "cancel"} else []
    url = reverse(f"payments:{action}", args=args)
    assert client.post(url, payload).status_code == 403
    page = client.get(payment.get_absolute_url() if action == "cancel" else url)
    assert b"csrfmiddlewaretoken" in page.content
    token = client.cookies["csrftoken"].value
    if action == "payer_create":
        data = {"full_name": "Плательщик CSRF"}
    elif action == "invoice_create":
        data = {
            "payer": invoice.payer_id,
            "account_number": "CSRF",
            "amount_cents": 1000,
        }
    else:
        data = payload
    assert client.post(url, data, HTTP_X_CSRFTOKEN=token).status_code == 302
