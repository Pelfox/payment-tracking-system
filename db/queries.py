import re
from contextlib import closing
from decimal import Decimal
from pathlib import Path

from db.database import SQL_DIR, get_connection
from payment_tracking_system.models import PaymentResult

_parts = re.split(
    r"^-- name: (\w+)\s*$",
    (SQL_DIR / "queries.sql").read_text("utf-8"),
    flags=re.MULTILINE,
)
QUERIES = dict(zip(_parts[1::2], _parts[2::2]))


def fetch_all(database: str | Path, name: str, parameters: dict | None = None):
    with closing(get_connection(database)) as connection:
        return connection.execute(QUERIES[name], parameters or {}).fetchall()


def rubles(cents) -> Decimal:
    return Decimal(str(cents)) / 100


def payment_result(row) -> PaymentResult:
    return PaymentResult.model_validate(
        {
            "payment": {
                "id": row["id"],
                "payer": {"full_name": row["full_name"]},
                "invoice": {
                    "account_number": row["account_number"],
                    "amount": rubles(row["invoice_amount_cents"]),
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
