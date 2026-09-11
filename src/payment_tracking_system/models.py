from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

# Положительная денежная сумма: используется для счетов и платежей.
Money = Annotated[
    Decimal,
    Field(gt=0, max_digits=12, decimal_places=2),
]

# Неотрицательная сумма: расчётный результат может быть равен нулю.
NonNegativeMoney = Annotated[
    Decimal,
    Field(ge=0, max_digits=14, decimal_places=2),
]


class PaymentMethod(StrEnum):
    """Поддерживаемые способы оплаты."""

    CARD = "карта"
    CASH = "наличные"


class PaymentStatus(StrEnum):
    """Результат сравнения суммы платежа с суммой счёта."""

    PAID = "оплачен полностью"
    PARTIALLY_PAID = "частичная оплата"
    OVERPAID = "переплата"


class Payer(BaseModel):
    """Плательщик, от имени которого совершается платёж."""

    full_name: str = Field(min_length=1)


class Invoice(BaseModel):
    """Счёт, выставленный плательщику."""

    account_number: str = Field(min_length=1)
    amount: Money


class Payment(BaseModel):
    """Платёж и связанная с ним информация предметной области."""

    id: UUID = Field(default_factory=uuid4)
    payer: Payer
    invoice: Invoice
    amount: Money
    method: PaymentMethod
    paid_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: PaymentStatus | None = None


class PaymentResult(BaseModel):
    """Итог обработки платежа со всеми рассчитанными значениями."""

    payment: Payment
    status: PaymentStatus
    commission: NonNegativeMoney
    total_charged: NonNegativeMoney
    remaining_amount: NonNegativeMoney
    overpayment: NonNegativeMoney
