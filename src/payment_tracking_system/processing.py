from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal

from payment_tracking_system.models import (
    Payment,
    PaymentMethod,
    PaymentResult,
    PaymentStatus,
)

CARD_COMMISSION_RATE = Decimal("0.01")
MONEY_QUANTUM = Decimal("0.01")
ZERO_MONEY = Decimal("0.00")


def calculate_commission(payment: Payment) -> Decimal:
    """Рассчитать комиссию для выбранного способа оплаты."""
    if payment.method is PaymentMethod.CARD:
        return (payment.amount * CARD_COMMISSION_RATE).quantize(
            MONEY_QUANTUM,
            rounding=ROUND_HALF_UP,
        )
    return ZERO_MONEY


def determine_payment_status(payment: Payment) -> PaymentStatus:
    """Определить статус платежа относительно суммы счёта."""
    if payment.amount == payment.invoice.amount:
        return PaymentStatus.PAID
    if payment.amount < payment.invoice.amount:
        return PaymentStatus.PARTIALLY_PAID
    return PaymentStatus.OVERPAID


def calculate_remaining_amount(payment: Payment) -> Decimal:
    """Рассчитать сумму, которую осталось внести по счёту."""
    difference = payment.invoice.amount - payment.amount
    return max(difference, ZERO_MONEY)


def calculate_overpayment(payment: Payment) -> Decimal:
    """Рассчитать сумму переплаты по счёту."""
    difference = payment.amount - payment.invoice.amount
    return max(difference, ZERO_MONEY)


def process_payment(payment: Payment) -> PaymentResult:
    """Выполнить все расчёты для платежа и вернуть их результат."""
    commission = calculate_commission(payment)
    status = determine_payment_status(payment)
    processed_payment = payment.model_copy(update={"status": status})

    return PaymentResult(
        payment=processed_payment,
        status=status,
        commission=commission,
        total_charged=payment.amount + commission,
        remaining_amount=calculate_remaining_amount(payment),
        overpayment=calculate_overpayment(payment),
    )


def process_payments(payments: Iterable[Payment]) -> list[PaymentResult]:
    """Последовательно обработать набор платежей."""
    results: list[PaymentResult] = []

    for payment in payments:
        results.append(process_payment(payment))

    return results
