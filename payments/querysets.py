import re
from datetime import datetime, time, timedelta

from django.db import models
from django.utils import timezone

from payment_tracking_system.models import PaymentStatus


class PaymentQuerySet(models.QuerySet):
    def active(self):
        return self.filter(cancelled_at__isnull=True)

    def cancelled(self):
        return self.filter(cancelled_at__isnull=False)

    def with_related(self):
        return self.select_related("invoice__payer")

    def paid(self):
        return self.filter(status=PaymentStatus.PAID.value)

    def partially_paid(self):
        return self.filter(status=PaymentStatus.PARTIALLY_PAID.value)

    def overpaid(self):
        return self.filter(status=PaymentStatus.OVERPAID.value)

    def for_date(self, selected_date):
        start = timezone.make_aware(datetime.combine(selected_date, time.min))
        end = timezone.make_aware(
            datetime.combine(selected_date + timedelta(days=1), time.min)
        )
        return self.filter(paid_at__gte=start, paid_at__lt=end)

    def search(self, text):
        pattern = re.escape(text)
        return self.filter(
            models.Q(invoice__payer__full_name__iregex=pattern)
            | models.Q(invoice__account_number__iregex=pattern)
        )

    def with_labels(self):
        return self.annotate(
            payer_name=models.F("invoice__payer__full_name"),
            account_number=models.F("invoice__account_number"),
        )


class PaymentManager(models.Manager.from_queryset(PaymentQuerySet)):
    """Все операции доступны по умолчанию; выборки можно объединять в цепочки."""
