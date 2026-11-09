import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from io import StringIO
from uuid import UUID

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, connection

from db.database import initialize_database
from db.import_sqlite_to_orm import import_payments
from db.inspect_source_db import inspect_database
from payments.models import Invoice, Payer, Payment

PAYMENT_ID = UUID("12345678-1234-5678-1234-567812345678")


@pytest.fixture
def legacy_database(tmp_path):
    path = tmp_path / "source.sqlite"
    initialize_database(path)
    with closing(sqlite3.connect(path)) as legacy, legacy:
        legacy.executemany(
            "INSERT INTO payers(id, full_name) VALUES (?, ?)",
            [(7, "Без счетов"), (42, "Иван Иванов")],
        )
        legacy.executemany(
            "INSERT INTO invoices(id, payer_id, account_number, amount_cents) "
            "VALUES (?, ?, ?, ?)",
            [(77, 42, "ACC-001", 10000), (78, 42, "EMPTY", 20000)],
        )
        legacy.execute(
            "INSERT INTO payments VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(PAYMENT_ID),
                77,
                6000,
                "карта",
                "2026-11-01T12:00:00+03:00",
                "частичная оплата",
                60,
                6060,
                4000,
                0,
            ),
        )
    return path


def test_inspection_and_import_preserve_ids_relationships_and_source(
    orm_database,
    legacy_database,
):
    original = legacy_database.read_bytes()
    assert {t["name"]: t["count"] for t in inspect_database(legacy_database)} == {
        "payers": 2,
        "invoices": 2,
        "payments": 1,
    }
    assert import_payments(legacy_database) == {
        "payers": 2,
        "invoices": 2,
        "payments": 1,
    }
    payment = Payment.objects.select_related("invoice__payer").get(pk=PAYMENT_ID)
    assert payment.invoice_id == 77
    assert payment.invoice.payer_id == 42
    assert payment.invoice.payer.full_name == "Иван Иванов"
    assert payment.paid_at == datetime(2026, 11, 1, 9, tzinfo=UTC)
    assert payment.commission_cents == 60
    assert payment.remaining_cents == 4000
    assert Invoice.objects.get(pk=78).payments.count() == 0
    assert Payer.objects.get(pk=7).invoices.count() == 0
    assert legacy_database.read_bytes() == original


def test_repeated_import_updates_without_duplicates_or_deleting_other_data(
    orm_database,
    legacy_database,
):
    import_payments(legacy_database)
    extra = Payer.objects.create(full_name="Новый плательщик")
    with closing(sqlite3.connect(legacy_database)) as legacy, legacy:
        legacy.execute(
            "UPDATE payments SET amount_cents=10000, status='оплачен полностью', "
            "commission_cents=100, total_charged_cents=10100, remaining_cents=0"
        )
    expected = {"payers": 3, "invoices": 2, "payments": 1}
    assert import_payments(legacy_database) == expected
    assert import_payments(legacy_database) == expected
    assert Payment.objects.get(pk=PAYMENT_ID).amount_cents == 10000
    assert Payer.objects.filter(pk=extra.pk).exists()


def test_import_is_atomic_on_conflicting_target_identity(orm_database, legacy_database):
    Payer.objects.create(pk=99, full_name="Иван Иванов")
    with pytest.raises(IntegrityError):
        import_payments(legacy_database)
    assert list(Payer.objects.values_list("pk", flat=True)) == [99]
    assert Invoice.objects.count() == Payment.objects.count() == 0


def test_invalid_unused_invoice_rolls_back_prior_imports(orm_database, legacy_database):
    with closing(sqlite3.connect(legacy_database)) as legacy, legacy:
        legacy.execute("UPDATE invoices SET account_number=? WHERE id=78", ("X" * 101,))
    with pytest.raises(ValidationError):
        import_payments(legacy_database)
    assert (
        Payer.objects.count() == Invoice.objects.count() == Payment.objects.count() == 0
    )


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE invoices SET payer_id=999 WHERE id=77",
        "UPDATE payments SET paid_at='invalid'",
        "UPDATE payments SET commission_cents=100, total_charged_cents=6100",
        "UPDATE payments SET id='invalid-uuid'",
    ],
)
def test_invalid_source_is_rejected_before_any_writes(
    orm_database,
    legacy_database,
    statement,
):
    with closing(sqlite3.connect(legacy_database)) as legacy, legacy:
        legacy.execute(statement)
    with pytest.raises(ValueError):
        import_payments(legacy_database)
    assert (
        Payer.objects.count() == Invoice.objects.count() == Payment.objects.count() == 0
    )


def test_missing_source_does_not_create_a_file(orm_database, tmp_path):
    source = tmp_path / "missing.sqlite"
    with pytest.raises(sqlite3.OperationalError):
        import_payments(source)
    assert not source.exists()
    with pytest.raises(CommandError):
        call_command("import_legacy_payments", source=source, stdout=StringIO())


def test_source_must_not_be_target_database(orm_database):
    with pytest.raises(ValueError, match="отличаться"):
        import_payments(connection.settings_dict["NAME"])


def test_management_command_imports_data(orm_database, legacy_database):
    output = StringIO()
    call_command("import_legacy_payments", source=legacy_database, stdout=output)
    assert "Перенос завершён" in output.getvalue()
    assert Payment.objects.count() == 1


def test_naive_legacy_timestamp_is_interpreted_as_utc(orm_database, legacy_database):
    with closing(sqlite3.connect(legacy_database)) as legacy, legacy:
        legacy.execute("UPDATE payments SET paid_at='2026-11-01T12:00:00'")
    import_payments(legacy_database)
    assert Payment.objects.get(pk=PAYMENT_ID).paid_at == datetime(
        2026, 11, 1, 12, tzinfo=UTC
    )
