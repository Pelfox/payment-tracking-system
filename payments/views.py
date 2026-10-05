from collections import Counter
from decimal import Decimal
from uuid import UUID

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_safe

from payment_tracking_system.exceptions import PaymentDataLoadError
from payment_tracking_system.models import PaymentStatus
from payment_tracking_system.repositories import JsonPaymentRepository


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


@require_safe
def payment_list(request: HttpRequest) -> HttpResponse:
    try:
        results = JsonPaymentRepository(settings.PAYMENTS_FILE).get_all()
    except PaymentDataLoadError:
        return _storage_unavailable(request)

    return render(
        request,
        "payments/list.html",
        {"results": results},
    )


@require_safe
def payment_detail(request: HttpRequest, payment_id: UUID) -> HttpResponse:
    try:
        result = JsonPaymentRepository(settings.PAYMENTS_FILE).get_by_id(payment_id)
    except PaymentDataLoadError:
        return _storage_unavailable(request)

    if result is None:
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
        {"result": result},
    )


@require_safe
def payment_summary(request: HttpRequest) -> HttpResponse:
    try:
        results = JsonPaymentRepository(settings.PAYMENTS_FILE).get_all()
    except PaymentDataLoadError:
        return _storage_unavailable(request)

    counts = Counter(result.status for result in results)
    return render(
        request,
        "payments/summary.html",
        {
            "payment_count": len(results),
            "total_amount": sum((r.payment.amount for r in results), Decimal("0.00")),
            "total_commission": sum((r.commission for r in results), Decimal("0.00")),
            "status_counts": [(status, counts[status]) for status in PaymentStatus],
        },
    )
