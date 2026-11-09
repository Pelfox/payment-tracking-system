import logging
import re
from datetime import datetime, time, timedelta
from functools import wraps
from uuid import UUID

from django.core.paginator import Paginator
from django.db import DatabaseError
from django.db.models import Avg, Count, Max, Min, Q, Sum
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_safe

from payment_tracking_system.models import PaymentStatus

from .forms import PAYMENT_SORT_FIELDS, PayerStatisticsForm, PaymentSearchForm
from .models import Invoice, Payer, Payment, rubles

logger = logging.getLogger(__name__)
PAGE_SIZE = 50


def database_page(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        except DatabaseError:
            logger.exception("Не удалось получить данные платежей через ORM.")
            return render(
                request,
                "error.html",
                {
                    "title": "Платежи временно недоступны",
                    "message": "Не удалось загрузить данные. Попробуйте позже.",
                    "status_code": 503,
                },
                status=503,
            )

    return wrapped


def _payment_results(request, *, partial_only=False, search_only=False):
    payers = Payer.objects.order_by("full_name", "pk").values("id", "full_name")
    form = PaymentSearchForm(
        request.GET if request.GET or not search_only else None, payers=payers
    )
    payments = Payment.objects.select_related("invoice__payer")
    if partial_only:
        payments = payments.filter(status=PaymentStatus.PARTIALLY_PAID.value)

    valid = form.is_bound and form.is_valid()
    page_obj = None
    if valid:
        data = form.cleaned_data
        conditions = {key: data[key] for key in ("status", "method") if data[key]}
        payments = payments.filter(**conditions)
        if data["payer_id"] is not None:
            payments = payments.filter(invoice__payer_id=data["payer_id"])
        if data["min_amount"] is not None:
            payments = payments.filter(amount_cents__gte=int(data["min_amount"] * 100))
        if data["payer_name"]:
            payments = payments.filter(
                invoice__payer__full_name__iregex=re.escape(data["payer_name"])
            )
        if data["q"]:
            payments = payments.filter(
                Q(invoice__payer__full_name__iregex=re.escape(data["q"]))
                | Q(invoice__account_number__iregex=re.escape(data["q"]))
            )
        if data["exclude_status"]:
            payments = payments.exclude(status=data["exclude_status"])
        if data["date_from"]:
            start = timezone.make_aware(datetime.combine(data["date_from"], time.min))
            payments = payments.filter(paid_at__gte=start)
        if data["date_to"]:
            end = timezone.make_aware(
                datetime.combine(data["date_to"] + timedelta(days=1), time.min)
            )
            payments = payments.filter(paid_at__lt=end)
        payments = payments.order_by(*PAYMENT_SORT_FIELDS[data["sort"] or "date"])
        page_obj = Paginator(payments, PAGE_SIZE).get_page(request.GET.get("page"))

    return render(
        request,
        "payments/search.html" if search_only else "payments/list.html",
        {
            "form": form,
            "payments": page_obj.object_list if page_obj is not None else [],
            "page_obj": page_obj,
            "statuses": list(PaymentStatus),
            "partial_only": partial_only,
            "searched": form.is_bound,
        },
        status=400 if form.is_bound and not valid else 200,
    )


@require_safe
@database_page
def payment_list(request: HttpRequest) -> HttpResponse:
    return _payment_results(request)


@require_safe
@database_page
def payment_detail(request: HttpRequest, payment_id: UUID) -> HttpResponse:
    try:
        payment = get_object_or_404(
            Payment.objects.select_related("invoice__payer"), pk=payment_id
        )
    except Http404:
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
    return render(request, "payments/detail.html", {"payment": payment})


@require_safe
@database_page
def partial_payments(request: HttpRequest) -> HttpResponse:
    return _payment_results(request, partial_only=True)


@require_safe
@database_page
def payment_search(request: HttpRequest) -> HttpResponse:
    return _payment_results(request, search_only=True)


@require_safe
@database_page
def payer_list(request: HttpRequest) -> HttpResponse:
    payers = Payer.objects.annotate(
        invoice_count=Count("invoices", distinct=True),
        payment_count=Count("invoices__payments"),
    ).order_by("full_name", "pk")
    return render(request, "payments/payers.html", {"payers": payers})


@require_safe
@database_page
def invoice_list(request: HttpRequest) -> HttpResponse:
    invoices = Invoice.objects.select_related("payer").order_by("account_number", "pk")
    return render(request, "payments/invoices.html", {"invoices": invoices})


@require_safe
@database_page
def payment_summary(request: HttpRequest) -> HttpResponse:
    totals = Payment.objects.aggregate(
        total_amount_cents=Sum("amount_cents", default=0),
        total_commission_cents=Sum("commission_cents", default=0),
        min_amount_cents=Min("amount_cents", default=0),
        max_amount_cents=Max("amount_cents", default=0),
        avg_amount_cents=Avg("amount_cents", default=0),
    )
    partial_totals = Payment.objects.filter(
        status=PaymentStatus.PARTIALLY_PAID.value
    ).aggregate(remaining_cents=Sum("remaining_cents", default=0))
    overpaid_totals = Payment.objects.filter(
        status=PaymentStatus.OVERPAID.value
    ).aggregate(overpayment_cents=Sum("overpayment_cents", default=0))
    counts = {
        row["status"]: row["payment_count"]
        for row in Payment.objects.values("status").annotate(payment_count=Count("pk"))
    }
    methods = list(
        Payment.objects.values("method")
        .annotate(
            payment_count=Count("pk"),
            amount_cents=Sum("amount_cents"),
            commission_cents=Sum("commission_cents"),
        )
        .order_by("method")
    )
    for method in methods:
        method["amount"] = rubles(method["amount_cents"])
        method["commission"] = rubles(method["commission_cents"])
    form = PayerStatisticsForm(request.GET)
    valid = form.is_valid()
    payers = []
    if valid:
        payers = list(
            Payer.objects.annotate(
                payment_count=Count("invoices__payments"),
                amount_cents=Sum("invoices__payments__amount_cents", default=0),
            )
            .filter(payment_count__gt=form.cleaned_data.get("min_count") or 0)
            .order_by("-payment_count", "full_name", "pk")
        )
        for payer in payers:
            payer.payment_amount = rubles(payer.amount_cents)
    return render(
        request,
        "payments/summary.html",
        {
            "payment_count": Payment.objects.count(),
            "total_amount": rubles(totals["total_amount_cents"]),
            "total_commission": rubles(totals["total_commission_cents"]),
            "min_amount": rubles(totals["min_amount_cents"]),
            "max_amount": rubles(totals["max_amount_cents"]),
            "avg_amount": rubles(totals["avg_amount_cents"]),
            "total_remaining": rubles(partial_totals["remaining_cents"]),
            "total_overpayment": rubles(overpaid_totals["overpayment_cents"]),
            "invoices_without_payments": Invoice.objects.filter(
                payments__isnull=True
            ).count(),
            "status_counts": [(s, counts.get(s.value, 0)) for s in PaymentStatus],
            "method_totals": methods,
            "payer_statistics": payers,
            "statistics_form": form,
        },
        status=200 if valid else 400,
    )
