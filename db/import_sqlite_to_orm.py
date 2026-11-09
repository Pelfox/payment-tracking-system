import argparse
import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import DatabaseError, connection, transaction

from db.database import DATABASE_PATH, get_connection
from payment_tracking_system.models import PaymentResult
from payment_tracking_system.processing import process_payment


def import_payments(source: str | Path = DATABASE_PATH) -> dict[str, int]:
    from payments.models import Invoice, Payer, Payment, rubles

    source = Path(source).resolve()
    if source == Path(connection.settings_dict["NAME"]).resolve():
        raise ValueError("Исходная база должна отличаться от базы Django.")
    with closing(get_connection(source)) as legacy:
        legacy.execute("BEGIN")
        if legacy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Нарушена целостность исходной базы.")
        if legacy.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("В исходной базе обнаружены нарушенные внешние ключи.")
        payers = [dict(row) for row in legacy.execute("SELECT * FROM payers")]
        invoices = [dict(row) for row in legacy.execute("SELECT * FROM invoices")]
        payments = [dict(row) for row in legacy.execute("SELECT * FROM payments")]

    payer_map = {row["id"]: row for row in payers}
    invoice_map = {row["id"]: row for row in invoices}
    seen = set()
    for row in invoices:
        if row["payer_id"] not in payer_map:
            raise ValueError(f"Не найден плательщик счёта {row['id']}.")
        if type(row["amount_cents"]) is not int:
            raise ValueError("Сумма счёта должна быть целым числом копеек.")
    for row in payments:
        payment_id = UUID(row["id"])
        if payment_id in seen:
            raise ValueError(f"Повторный UUID платежа: {payment_id}.")
        seen.add(payment_id)
        if row["invoice_id"] not in invoice_map:
            raise ValueError(f"Не найден счёт платежа {payment_id}.")
        invoice = invoice_map[row["invoice_id"]]
        if any(
            type(value) is not int
            for key, value in row.items()
            if key.endswith("cents")
        ):
            raise ValueError("Суммы платежа должны быть целыми числами копеек.")
        paid_at = datetime.fromisoformat(row["paid_at"])
        if paid_at.tzinfo is None:
            paid_at = paid_at.replace(tzinfo=UTC)
        row["id"] = payment_id
        row["paid_at"] = paid_at.astimezone(UTC)
        result = PaymentResult.model_validate(
            {
                "payment": {
                    "id": payment_id,
                    "payer": {"full_name": payer_map[invoice["payer_id"]]["full_name"]},
                    "invoice": {
                        "account_number": invoice["account_number"],
                        "amount": rubles(invoice["amount_cents"]),
                    },
                    "amount": rubles(row["amount_cents"]),
                    "method": row["method"],
                    "paid_at": row["paid_at"],
                    "status": row["status"],
                },
                "status": row["status"],
                "commission": rubles(row["commission_cents"]),
                "total_charged": rubles(row["total_charged_cents"]),
                "remaining_amount": rubles(row["remaining_cents"]),
                "overpayment": rubles(row["overpayment_cents"]),
            }
        )
        if process_payment(result.payment) != result:
            raise ValueError(f"Некорректные итоги платежа: {payment_id}.")

    with transaction.atomic():
        for model, rows in ((Payer, payers), (Invoice, invoices), (Payment, payments)):
            for row in rows:
                values = {key: value for key, value in row.items() if key != "id"}
                instance, _ = model.objects.update_or_create(
                    pk=row["id"], defaults=values
                )
                instance.full_clean()
        return {
            "payers": Payer.objects.count(),
            "invoices": Invoice.objects.count(),
            "payments": Payment.objects.count(),
        }


def main():
    import django

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    django.setup()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DATABASE_PATH)
    args = parser.parse_args()
    try:
        counts = import_payments(args.source)
    except (
        OSError,
        ValueError,
        ValidationError,
        DatabaseError,
        sqlite3.Error,
    ) as error:
        parser.exit(1, f"Ошибка переноса: {error}\n")
    print(f"Перенос завершён. В базе Django: {counts}")


if __name__ == "__main__":
    main()
