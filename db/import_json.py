import argparse
import json
import sqlite3
from contextlib import closing
from datetime import UTC
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from db.database import (
    BASE_DIR,
    DATABASE_PATH,
    get_connection,
    initialize_database,
)
from payment_tracking_system.models import PaymentResult
from payment_tracking_system.processing import process_payment

PAYMENTS_FILE = BASE_DIR / "data" / "payments.json"


def load_results(source: str | Path) -> list[PaymentResult]:
    data = json.loads(Path(source).read_text(encoding="utf-8-sig"))
    results = TypeAdapter(list[PaymentResult]).validate_python(data)
    seen = set()
    for raw, result in zip(data, results, strict=True):
        payment = result.payment
        if not {"id", "paid_at"} <= raw["payment"].keys():
            raise ValueError("У каждого платежа должны быть id и paid_at.")
        if payment.id in seen:
            raise ValueError(f"Повторный ID платежа: {payment.id}")
        seen.add(payment.id)
        normalized = result.model_copy(
            update={
                "payment": payment.model_copy(update={"status": result.status}),
            }
        )
        if (
            payment.status is not None and payment.status != result.status
        ) or process_payment(payment) != normalized:
            raise ValueError(f"Некорректные итоги платежа: {payment.id}")
    return results


def import_payments(
    source: str | Path = PAYMENTS_FILE,
    database: str | Path = DATABASE_PATH,
) -> dict[str, int]:
    results = load_results(source)
    initialize_database(database)
    with closing(get_connection(database, readonly=False)) as connection:
        with connection:
            for result in results:
                payment = result.payment
                connection.execute(
                    "INSERT INTO payers (full_name) VALUES (?) "
                    "ON CONFLICT (full_name) DO NOTHING",
                    (payment.payer.full_name,),
                )
                payer_id = connection.execute(
                    "SELECT id FROM payers WHERE full_name = ?",
                    (payment.payer.full_name,),
                ).fetchone()["id"]
                invoice_values = (
                    payer_id,
                    payment.invoice.account_number,
                    int(payment.invoice.amount * 100),
                )
                connection.execute(
                    "INSERT INTO invoices "
                    "(payer_id, account_number, amount_cents) VALUES (?, ?, ?) "
                    "ON CONFLICT (payer_id, account_number, amount_cents) "
                    "DO NOTHING",
                    invoice_values,
                )
                invoice_id = connection.execute(
                    "SELECT id FROM invoices WHERE payer_id = ? "
                    "AND account_number = ? AND amount_cents = ?",
                    invoice_values,
                ).fetchone()["id"]
                paid_at = payment.paid_at
                if paid_at.tzinfo is None:
                    paid_at = paid_at.replace(tzinfo=UTC)
                connection.execute(
                    """
                    INSERT INTO payments (
                        id, invoice_id, amount_cents, method, paid_at, status,
                        commission_cents, total_charged_cents,
                        remaining_cents, overpayment_cents
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (id) DO UPDATE SET
                        invoice_id = excluded.invoice_id,
                        amount_cents = excluded.amount_cents,
                        method = excluded.method,
                        paid_at = excluded.paid_at,
                        status = excluded.status,
                        commission_cents = excluded.commission_cents,
                        total_charged_cents = excluded.total_charged_cents,
                        remaining_cents = excluded.remaining_cents,
                        overpayment_cents = excluded.overpayment_cents
                    """,
                    (
                        str(payment.id),
                        invoice_id,
                        int(payment.amount * 100),
                        payment.method.value,
                        paid_at.astimezone(UTC).isoformat(timespec="microseconds"),
                        result.status.value,
                        int(result.commission * 100),
                        int(result.total_charged * 100),
                        int(result.remaining_amount * 100),
                        int(result.overpayment * 100),
                    ),
                )
        return {
            "imported": len(results),
            "payers": connection.execute(
                "SELECT COUNT(*) AS count FROM payers"
            ).fetchone()["count"],
            "invoices": connection.execute(
                "SELECT COUNT(*) AS count FROM invoices"
            ).fetchone()["count"],
            "payments": connection.execute(
                "SELECT COUNT(*) AS count FROM payments"
            ).fetchone()["count"],
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Перенести платежи JSON в SQLite")
    parser.add_argument(
        "--source", type=Path, default=PAYMENTS_FILE, help="Путь к исходному JSON-файлу"
    )
    parser.add_argument(
        "--database", type=Path, default=DATABASE_PATH, help="Путь к базе SQLite"
    )
    args = parser.parse_args()
    try:
        counts = import_payments(args.source, args.database)
    except (OSError, ValueError, ValidationError, sqlite3.Error) as error:
        parser.exit(1, f"Ошибка импорта: {error}\n")
    print(f"Обработано платежей из JSON: {counts['imported']}")
    print(
        f"В базе: плательщиков — {counts['payers']}, "
        f"счетов — {counts['invoices']}, платежей — {counts['payments']}."
    )


if __name__ == "__main__":
    main()
