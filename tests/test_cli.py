from decimal import Decimal
from pathlib import Path

import pytest

import payment_tracking_system as cli
from payment_tracking_system.repositories import JsonPaymentRepository


@pytest.mark.parametrize(
    ("amount", "expected_status", "expected_message"),
    [
        ("1000.00", "оплачен полностью", "Итого списано: 1010.00 руб."),
        ("750.00", "частичная оплата", "Осталось оплатить: 250.00 руб."),
        ("1200.00", "переплата", "Сумма переплаты: 200.00 руб."),
    ],
)
def test_cli_saves_payment_and_displays_it_on_next_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    amount: str,
    expected_status: str,
    expected_message: str,
) -> None:
    payments_file = tmp_path / "data" / "payments.json"
    monkeypatch.setattr(cli, "PAYMENTS_FILE", payments_file)
    answers = iter(["Иван Иванов", "ACC-001", "1000.00", amount, "карта", "нет"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))

    cli.main()

    output = capsys.readouterr().out
    assert "Сохранённые платежи: 1" in output
    assert f"Статус: {expected_status}" in output
    assert expected_message in output
    saved = JsonPaymentRepository(payments_file).get_all()
    assert len(saved) == 1
    assert saved[0].payment.payer.full_name == "Иван Иванов"
    assert saved[0].payment.invoice.account_number == "ACC-001"
    assert saved[0].payment.amount == Decimal(amount)
    assert saved[0].status == expected_status

    # При следующем запуске EOF завершает ввод, но сохранённый платёж виден.
    def end_input(prompt: str) -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", end_input)
    cli.main()

    output = capsys.readouterr().out
    assert "Сохранённые платежи: 1" in output
    assert "Плательщик: Иван Иванов" in output
    assert expected_message in output
    assert JsonPaymentRepository(payments_file).get_all() == saved


def test_cli_retries_invalid_input_without_saving_invalid_payment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    payments_file = tmp_path / "payments.json"
    monkeypatch.setattr(cli, "PAYMENTS_FILE", payments_file)
    answers = iter(
        [
            "Иван Иванов",
            "ACC-001",
            "не число",
            "Пётр Петров",
            "ACC-002",
            "500.00",
            "500.00",
            "наличные",
            "нет",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))

    cli.main()

    output = capsys.readouterr().out
    assert "Ошибка ввода:" in output
    assert "Сохранённые платежи: 1" in output
    saved = JsonPaymentRepository(payments_file).get_all()
    assert len(saved) == 1
    assert saved[0].payment.payer.full_name == "Пётр Петров"
    assert saved[0].commission == Decimal("0.00")


def test_cli_reports_corrupt_storage_without_changing_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    payments_file = tmp_path / "payments.json"
    payments_file.write_text("{invalid json", encoding="utf-8")
    monkeypatch.setattr(cli, "PAYMENTS_FILE", payments_file)

    cli.main()

    assert "Ошибка хранилища:" in capsys.readouterr().out
    assert payments_file.read_text(encoding="utf-8") == "{invalid json"
