from decimal import Decimal
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone
from pydantic import ValidationError as DomainValidationError

from payment_tracking_system.models import Invoice as DomainInvoice
from payment_tracking_system.models import Payer as DomainPayer
from payment_tracking_system.models import Payment as DomainPayment
from payment_tracking_system.models import PaymentMethod, PaymentStatus
from payment_tracking_system.processing import process_payment

MAX_AMOUNT_CENTS = 999_999_999_999
NON_BLANK = RegexValidator(r"\S", "Значение не может состоять из пробелов.")


def rubles(cents) -> Decimal:
    return Decimal(str(cents)) / 100


class Payer(models.Model):
    full_name = models.CharField(
        "ФИО плательщика", max_length=200, unique=True, validators=[NON_BLANK]
    )

    class Meta:
        ordering = ["full_name", "pk"]
        verbose_name = "Плательщик"
        verbose_name_plural = "Плательщики"
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(full_name=""), name="payer_name_not_empty"
            )
        ]

    def __str__(self):
        return self.full_name


class Invoice(models.Model):
    payer = models.ForeignKey(
        Payer,
        on_delete=models.PROTECT,
        related_name="invoices",
        verbose_name="Плательщик",
    )
    account_number = models.CharField(
        "Номер счёта", max_length=100, validators=[NON_BLANK]
    )
    amount_cents = models.PositiveBigIntegerField(
        "Сумма счёта, коп.",
        validators=[MinValueValidator(1), MaxValueValidator(MAX_AMOUNT_CENTS)],
    )

    class Meta:
        ordering = ["account_number", "pk"]
        verbose_name = "Счёт"
        verbose_name_plural = "Счета"
        constraints = [
            models.UniqueConstraint(
                fields=["payer", "account_number", "amount_cents"],
                name="unique_invoice_snapshot",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    amount_cents__gt=0, amount_cents__lte=MAX_AMOUNT_CENTS
                ),
                name="invoice_positive_amount",
            ),
            models.CheckConstraint(
                condition=~models.Q(account_number=""), name="invoice_number_not_empty"
            ),
        ]

    @property
    def amount(self):
        return rubles(self.amount_cents)

    def __str__(self):
        return self.account_number


class Payment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    invoice = models.ForeignKey(
        Invoice, on_delete=models.PROTECT, related_name="payments", verbose_name="Счёт"
    )
    amount_cents = models.PositiveBigIntegerField(
        "Сумма платежа, коп.",
        validators=[MinValueValidator(1), MaxValueValidator(MAX_AMOUNT_CENTS)],
    )
    method = models.CharField(
        "Способ оплаты",
        max_length=20,
        choices=[(m.value, m.value) for m in PaymentMethod],
    )
    paid_at = models.DateTimeField("Дата платежа", default=timezone.now)
    status = models.CharField(
        "Статус",
        max_length=30,
        choices=[(s.value, s.value) for s in PaymentStatus],
        editable=False,
    )
    commission_cents = models.PositiveBigIntegerField("Комиссия, коп.", editable=False)
    total_charged_cents = models.PositiveBigIntegerField(
        "Итого списано, коп.", editable=False
    )
    remaining_cents = models.PositiveBigIntegerField("Остаток, коп.", editable=False)
    overpayment_cents = models.PositiveBigIntegerField(
        "Переплата, коп.", editable=False
    )

    class Meta:
        ordering = ["-paid_at", "id"]
        verbose_name = "Платёж"
        verbose_name_plural = "Платежи"
        indexes = [
            models.Index(fields=["-paid_at", "id"], name="payment_date_idx"),
            models.Index(fields=["status"], name="payment_status_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    amount_cents__gt=0, amount_cents__lte=MAX_AMOUNT_CENTS
                ),
                name="payment_positive_amount",
            ),
            models.CheckConstraint(
                condition=models.Q(method__in=[m.value for m in PaymentMethod]),
                name="payment_valid_method",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=[s.value for s in PaymentStatus]),
                name="payment_valid_status",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    total_charged_cents=models.F("amount_cents")
                    + models.F("commission_cents")
                ),
                name="payment_total_matches",
            ),
        ]

    @property
    def amount(self):
        return rubles(self.amount_cents)

    @property
    def commission(self):
        return rubles(self.commission_cents)

    @property
    def total_charged(self):
        return rubles(self.total_charged_cents)

    @property
    def remaining_amount(self):
        return rubles(self.remaining_cents)

    @property
    def overpayment(self):
        return rubles(self.overpayment_cents)

    @property
    def status_enum(self):
        return PaymentStatus(self.status)

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and not update_fields:
            return
        calculation = self
        if update_fields is not None:
            # Не включаем в расчёт изменения, которые вызывающий код не сохраняет.
            calculation = (
                type(self)
                .objects.using(kwargs.get("using") or self._state.db)
                .select_related("invoice__payer")
                .get(pk=self.pk)
            )
            for field in ("amount_cents", "method", "paid_at"):
                if field in update_fields:
                    setattr(calculation, field, getattr(self, field))
            if {"invoice", "invoice_id"} & set(update_fields):
                calculation.invoice = self.invoice
        try:
            result = process_payment(
                DomainPayment(
                    id=self.id,
                    payer=DomainPayer(full_name=calculation.invoice.payer.full_name),
                    invoice=DomainInvoice(
                        account_number=calculation.invoice.account_number,
                        amount=calculation.invoice.amount,
                    ),
                    amount=calculation.amount,
                    method=calculation.method,
                    paid_at=calculation.paid_at,
                )
            )
        except DomainValidationError as error:
            raise ValidationError(str(error)) from error
        self.status = result.status.value
        for field, value in (
            ("commission_cents", result.commission),
            ("total_charged_cents", result.total_charged),
            ("remaining_cents", result.remaining_amount),
            ("overpayment_cents", result.overpayment),
        ):
            setattr(self, field, int(value * 100))
        if update_fields is not None:
            kwargs["update_fields"] = set(update_fields) | {
                "status",
                "commission_cents",
                "total_charged_cents",
                "remaining_cents",
                "overpayment_cents",
            }
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.id}: {self.amount} руб."
