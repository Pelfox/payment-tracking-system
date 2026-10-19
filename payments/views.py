import logging
import sqlite3
from datetime import UTC, datetime, time, timedelta
from functools import wraps
from uuid import UUID

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_safe

from db.queries import fetch_all, payment_result, rubles
from payment_tracking_system.models import PaymentStatus

from .forms import PayerStatisticsForm, PaymentSearchForm

logger = logging.getLogger(__name__)
PAGE_SIZE = 50


def _storage_unavailable(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "error.html",
        {
            "title": "Платежи временно недоступны",
            "message": (
                "Не удалось загрузить данные. Попробуйте открыть страницу позже."
            ),
            "status_code": 503,
        },
        status=503,
    )


def database_page(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        except (sqlite3.Error, OSError):
            logger.exception("Не удалось получить данные платежей SQLite.")
            return _storage_unavailable(request)

    return wrapped


def query(name, parameters=None):
    return fetch_all(settings.PAYMENTS_DATABASE, name, parameters)


@require_safe
@database_page
def payment_list(request: HttpRequest) -> HttpResponse:
    results = [payment_result(row) for row in query("all_payments")]
    return render(
        request,
        "payments/list.html",
        {
            "results": results,
            "statuses": list(PaymentStatus),
        },
    )


@require_safe
@database_page
def payment_detail(request: HttpRequest, payment_id: UUID) -> HttpResponse:
    rows = query("payment_detail", {"payment_id": str(payment_id)})
    if not rows:
        return render(
            request,
            "error.html",
            {
                "title": "Платёж не найден",
                "message": (
                    "Платежа с таким идентификатором нет среди сохранённых операций."
                ),
                "status_code": 404,
                "back_to_payments": True,
            },
            status=404,
        )
    return render(
        request,
        "payments/detail.html",
        {
            "result": payment_result(rows[0]),
        },
    )


@require_safe
@database_page
def partial_payments(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "payments/list.html",
        {
            "results": [payment_result(row) for row in query("partial_payments")],
            "statuses": list(PaymentStatus),
            "partial_only": True,
        },
    )


@require_safe
@database_page
def payment_search(request: HttpRequest) -> HttpResponse:
    form = PaymentSearchForm(request.GET or None, payers=query("payers"))
    context = {"form": form, "results": [], "searched": form.is_bound}
    if form.is_bound and form.is_valid():
        data = form.cleaned_data
        page = data.get("page") or 1
        parameters = {
            "status": data["status"] or None,
            "method": data["method"] or None,
            "payer_id": data["payer_id"],
            "date_from": None,
            "date_to": None,
            "min_amount": (
                int(data["min_amount"] * 100)
                if data["min_amount"] is not None
                else None
            ),
            "limit": PAGE_SIZE + 1,
            "offset": (page - 1) * PAGE_SIZE,
        }

        for field in ("date_from", "date_to"):
            value = data[field]
            if value:
                if field == "date_to":
                    value += timedelta(days=1)
                boundary = timezone.make_aware(datetime.combine(value, time.min))
                parameters[field] = boundary.astimezone(UTC).isoformat(
                    timespec="microseconds"
                )
        rows = query("search_payments", parameters)
        context["results"] = [payment_result(row) for row in rows[:PAGE_SIZE]]
        context["page"] = page
        for direction, number, visible in (
            ("previous", page - 1, page > 1),
            ("next", page + 1, len(rows) > PAGE_SIZE),
        ):
            if visible:
                params = request.GET.copy()
                params["page"] = number
                context[f"{direction}_query"] = params.urlencode()

    invalid = form.is_bound and bool(form.errors)
    return render(
        request, "payments/search.html", context, status=400 if invalid else 200
    )


@require_safe
@database_page
def payer_list(request: HttpRequest) -> HttpResponse:
    return render(request, "payments/payers.html", {"payers": query("payers")})


@require_safe
@database_page
def invoice_list(request: HttpRequest) -> HttpResponse:
    invoices = [dict(row) for row in query("invoices")]
    payers = {row["id"]: row["full_name"] for row in query("payers")}
    for invoice in invoices:
        invoice["amount"] = rubles(invoice["amount_cents"])
        invoice["payer_name"] = payers[invoice["payer_id"]]
    return render(request, "payments/invoices.html", {"invoices": invoices})


@require_safe
@database_page
def payment_summary(request: HttpRequest) -> HttpResponse:
    totals = query("payment_totals")[0]
    counts = {row["status"]: row["payment_count"] for row in query("status_counts")}
    methods = [dict(row) for row in query("method_totals")]
    for method in methods:
        method["amount"] = rubles(method["amount_cents"])
        method["commission"] = rubles(method["commission_cents"])
    form = PayerStatisticsForm(request.GET)
    valid = form.is_valid()
    min_count = (form.cleaned_data.get("min_count") or 0) if valid else 0
    payers = (
        [
            dict(row)
            for row in query(
                "payer_statistics",
                {
                    "min_count": min_count,
                },
            )
        ]
        if valid
        else []
    )
    for payer in payers:
        payer["amount"] = rubles(payer["amount_cents"])
    return render(
        request,
        "payments/summary.html",
        {
            "payment_count": query("payment_count")[0]["payment_count"],
            "total_amount": rubles(totals["total_amount_cents"]),
            "total_commission": rubles(totals["total_commission_cents"]),
            "min_amount": rubles(totals["min_amount_cents"]),
            "max_amount": rubles(totals["max_amount_cents"]),
            "avg_amount": rubles(totals["avg_amount_cents"]),
            "status_counts": [(s, counts.get(s.value, 0)) for s in PaymentStatus],
            "method_totals": methods,
            "payer_statistics": payers,
            "statistics_form": form,
        },
        status=200 if valid else 400,
    )
