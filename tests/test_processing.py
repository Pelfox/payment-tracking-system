from decimal import Decimal

import pytest

from payment_tracking_system.models import Invoice, Payer, Payment, PaymentMethod
from payment_tracking_system.processing import process_payment, process_payments


@pytest.mark.parametrize(
    ("invoice_amount", "payment_amount", "expected_status", "remaining", "overpayment"),
    [
        ("1000.00", "1000.00", "оплачен полностью", "0.00", "0.00"),
        ("1000.00", "750.00", "частичная оплата", "250.00", "0.00"),
        ("1000.00", "1200.00", "переплата", "0.00", "200.00"),
    ],
)
def test_process_payment_status_and_difference(
    invoice_amount: str,
    payment_amount: str,
    expected_status: str,
    remaining: str,
    overpayment: str,
) -> None:
    payment = Payment(
        payer=Payer(full_name="Иван Иванов"),
        invoice=Invoice(account_number="ACC-001", amount=Decimal(invoice_amount)),
        amount=Decimal(payment_amount),
        method=PaymentMethod.CASH,
    )

    result = process_payment(payment)

    assert result.status == expected_status
    assert result.remaining_amount == Decimal(remaining)
    assert result.overpayment == Decimal(overpayment)


def test_process_payment_calculates_card_commission() -> None:
    payment = Payment(
        payer=Payer(full_name="Иван Иванов"),
        invoice=Invoice(account_number="ACC-001", amount=Decimal("1000.00")),
        amount=Decimal("750.00"),
        method=PaymentMethod.CARD,
    )

    result = process_payment(payment)

    assert result.commission == Decimal("7.50")
    assert result.total_charged == Decimal("757.50")
    assert result.payment.status == result.status


def test_process_payments_processes_every_payment_in_order() -> None:
    payments = [
        Payment(
            payer=Payer(full_name="Иван Иванов"),
            invoice=Invoice(account_number="ACC-001", amount=Decimal("1000.00")),
            amount=Decimal("750.00"),
            method=PaymentMethod.CASH,
        ),
        Payment(
            payer=Payer(full_name="Пётр Петров"),
            invoice=Invoice(account_number="ACC-002", amount=Decimal("500.00")),
            amount=Decimal("600.00"),
            method=PaymentMethod.CARD,
        ),
    ]

    results = process_payments(payments)

    assert len(results) == 2
    assert results[0].payment.id == payments[0].id
    assert results[1].payment.id == payments[1].id
    assert results[0].status == "частичная оплата"
    assert results[1].status == "переплата"
