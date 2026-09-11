from decimal import Decimal
from pathlib import Path

import pytest

from payment_tracking_system.exceptions import (
    PaymentDataLoadError,
    PaymentDataSaveError,
)
from payment_tracking_system.models import (
    Invoice,
    Payer,
    Payment,
    PaymentMethod,
    PaymentResult,
)
from payment_tracking_system.processing import process_payment
from payment_tracking_system.repositories import (
    InMemoryPaymentRepository,
    JsonPaymentRepository,
)


def create_result() -> PaymentResult:
    payment = Payment(
        payer=Payer(full_name="Иван Иванов"),
        invoice=Invoice(account_number="ACC-001", amount=Decimal("1000.00")),
        amount=Decimal("750.00"),
        method=PaymentMethod.CARD,
    )
    return process_payment(payment)


def test_repository_saves_and_returns_payment() -> None:
    repository = InMemoryPaymentRepository()
    result = create_result()

    repository.save(result)

    assert repository.count() == 1
    assert repository.get_by_id(result.payment.id) == result
    assert repository.get_all() == (result,)


def test_repository_updates_payment_without_duplicate() -> None:
    repository = InMemoryPaymentRepository()
    result = create_result()

    repository.save(result)
    repository.save(result)

    assert repository.count() == 1


def test_json_repository_saves_and_loads_payments(tmp_path: Path) -> None:
    file_path = tmp_path / "payments.json"
    result = create_result()

    repository = JsonPaymentRepository(file_path)
    repository.save(result)
    loaded_repository = JsonPaymentRepository(file_path)

    assert loaded_repository.count() == 1
    assert loaded_repository.get_by_id(result.payment.id) == result


def test_json_repository_reports_invalid_json(tmp_path: Path) -> None:
    file_path = tmp_path / "payments.json"
    file_path.write_text("{invalid json", encoding="utf-8")

    with pytest.raises(PaymentDataLoadError):
        JsonPaymentRepository(file_path)


def test_json_repository_reports_write_error(tmp_path: Path) -> None:
    file_instead_of_directory = tmp_path / "data"
    file_instead_of_directory.write_text("blocked", encoding="utf-8")
    repository = JsonPaymentRepository(file_instead_of_directory / "payments.json")

    with pytest.raises(PaymentDataSaveError):
        repository.save(create_result())
