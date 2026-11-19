# ruff: noqa: RUF012
from decimal import Decimal
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.core.validators import (
    FileExtensionValidator,
    MaxValueValidator,
    MinValueValidator,
    RegexValidator,
)
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from pydantic import ValidationError as DomainValidationError
from unidecode import unidecode

from payment_tracking_system.models import Invoice as DomainInvoice
from payment_tracking_system.models import Payer as DomainPayer
from payment_tracking_system.models import Payment as DomainPayment
from payment_tracking_system.models import PaymentMethod, PaymentStatus
from payment_tracking_system.processing import process_payment

from .querysets import PaymentManager

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
        help_text="Введите сумму в копейках: 10000 означает 100 рублей.",
    )
    slug = models.SlugField(
        "URL-идентификатор", max_length=180, unique=True, editable=False
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

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(unidecode(self.account_number))[:140] or "invoice"
            self.slug = f"{base}-{uuid4().hex}"
            if kwargs.get("update_fields"):
                kwargs["update_fields"] = set(kwargs["update_fields"]) | {"slug"}
        return super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("payments:invoice_detail", kwargs={"slug": self.slug})


class Payment(models.Model):
    class Status(models.TextChoices):
        PAID = PaymentStatus.PAID.value, "Оплачен полностью"
        PARTIALLY_PAID = PaymentStatus.PARTIALLY_PAID.value, "Частичная оплата"
        OVERPAID = PaymentStatus.OVERPAID.value, "Переплата"

    class Method(models.TextChoices):
        CARD = PaymentMethod.CARD.value, "Карта"
        CASH = PaymentMethod.CASH.value, "Наличные"

    objects = PaymentManager()

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    invoice = models.ForeignKey(
        Invoice, on_delete=models.PROTECT, related_name="payments", verbose_name="Счёт"
    )
    amount_cents = models.PositiveBigIntegerField(
        "Сумма платежа, коп.",
        validators=[MinValueValidator(1), MaxValueValidator(MAX_AMOUNT_CENTS)],
        help_text=(
            "Сумма одной операции в копейках. Итоги рассчитываются автоматически."
        ),
    )
    method = models.CharField(
        "Способ оплаты",
        max_length=20,
        choices=Method.choices,
    )
    paid_at = models.DateTimeField("Дата платежа", default=timezone.now)
    cancelled_at = models.DateTimeField("Дата отмены записи", null=True, editable=False)
    receipt = models.ImageField(
        "Изображение квитанции",
        upload_to="receipts/%Y/%m/",
        blank=True,
        validators=[FileExtensionValidator(["png", "jpg", "jpeg", "webp"])],
    )
    status = models.CharField(
        "Статус",
        max_length=30,
        choices=Status.choices,
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
    has_commission = models.GeneratedField(
        expression=models.Case(
            models.When(commission_cents__gt=0, then=models.Value(True)),
            default=models.Value(False),
            output_field=models.BooleanField(),
        ),
        output_field=models.BooleanField(),
        db_persist=True,
        verbose_name="Есть комиссия",
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

    @property
    def is_cancelled(self):
        return self.cancelled_at is not None

    def cancel(self):
        """Исключить запись из учёта, сохранив операцию и результаты расчёта."""
        cancelled_at = timezone.now()
        changed = (
            type(self)
            .objects.active()
            .filter(pk=self.pk)
            .update(cancelled_at=cancelled_at)
        )
        if changed:
            self.cancelled_at = cancelled_at
        return bool(changed)

    def get_absolute_url(self):
        return reverse("payments:detail", kwargs={"payment_id": self.pk})

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
