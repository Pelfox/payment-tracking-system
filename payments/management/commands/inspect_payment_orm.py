from django.core.management.base import BaseCommand
from django.db import connection
from django.db.models import Count
from django.test.utils import CaptureQueriesContext

from payments.models import Payer, Payment


class Command(BaseCommand):
    help = "Показать SQL и сравнить количество запросов без изменения данных."

    def handle(self, *args, **options):
        self.stdout.write(f"Всего операций: {Payment.objects.count()}")
        for label, queryset in (
            ("Полностью оплаченные", Payment.objects.paid()),
            ("Частично оплаченные", Payment.objects.partially_paid()),
            ("С переплатой", Payment.objects.overpaid()),
        ):
            self.stdout.write(f"{label}: {queryset.count()}")

        for label, queryset in (
            ("Без select_related", Payment.objects.all()),
            ("С select_related", Payment.objects.with_related()),
        ):
            with CaptureQueriesContext(connection) as captured:
                for payment in queryset[:20]:
                    str(payment.invoice.payer)
            self.stdout.write(f"{label}: {len(captured)} SQL-запросов (до 20 операций)")

        projection = (
            Payment.objects.exclude(amount_cents=0)
            .with_labels()
            .order_by("-paid_at", "pk")
            .values("id", "payer_name", "account_number", "amount_cents")[:20]
        )
        self.stdout.write(f"F + annotate + values:\n{projection.query}")
        self.stdout.write(f"План запроса:\n{projection.explain()}")

        payers = Payer.objects.annotate(
            payment_count=Count("invoices__payments")
        ).filter(payment_count__gt=0)
        self.stdout.write(f"GROUP BY + HAVING:\n{payers.query}")
