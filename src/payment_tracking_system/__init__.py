from decimal import Decimal, InvalidOperation
from pathlib import Path

from pydantic import ValidationError

from payment_tracking_system.exceptions import PaymentStorageError
from payment_tracking_system.models import (
    Invoice,
    Payer,
    Payment,
    PaymentMethod,
    PaymentResult,
    PaymentStatus,
)
from payment_tracking_system.processing import process_payments
from payment_tracking_system.repositories import (
    JsonPaymentRepository,
    PaymentRepository,
)

PAYMENTS_FILE = Path("data/payments.json")


def print_payment(result: PaymentResult) -> None:
    payment = result.payment

    print("\n=== Информация о платеже ===")
    print("Плательщик:", payment.payer.full_name)
    print("Номер счёта:", payment.invoice.account_number)
    print("Сумма счёта:", payment.invoice.amount, "руб.")
    print("Сумма платежа:", payment.amount, "руб.")
    print("Комиссия:", result.commission, "руб.")
    print("Итого списано:", result.total_charged, "руб.")
    print("Дата платежа:", payment.paid_at)
    print("Статус:", result.status.value)

    if result.status is PaymentStatus.PARTIALLY_PAID:
        print("Осталось оплатить:", result.remaining_amount, "руб.")
    elif result.status is PaymentStatus.OVERPAID:
        print("Сумма переплаты:", result.overpayment, "руб.")


def input_payment() -> Payment:
    """Запросить у пользователя данные одного платежа."""
    payer = Payer(full_name=input("Введите ФИО плательщика: "))
    invoice = Invoice(
        account_number=input("Введите номер счёта: "),
        amount=Decimal(input("Введите сумму счёта: ")),
    )
    return Payment(
        payer=payer,
        invoice=invoice,
        amount=Decimal(input("Введите сумму платежа: ")),
        method=PaymentMethod(input("Способ оплаты (карта/наличные): ").lower()),
    )


def run(repository: PaymentRepository) -> None:
    print("=== Система учёта платежей ===")
    payments: list[Payment] = []

    while True:
        try:
            payments.append(input_payment())
        except (InvalidOperation, ValueError, ValidationError) as error:
            print(f"Ошибка ввода: {error}")
            print("Попробуйте ввести платёж ещё раз.\n")
            continue
        except (EOFError, KeyboardInterrupt):
            print("\nВвод платежей завершён.")
            break

        try:
            answer = input("Добавить ещё один платёж? (да/нет): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nВвод платежей завершён.")
            break

        if answer not in {"да", "д"}:
            break

    for result in process_payments(payments):
        repository.save(result)

    print(f"\n=== Сохранённые платежи: {repository.count()} ===")
    for result in repository.get_all():
        print_payment(result)


def main() -> None:
    try:
        run(JsonPaymentRepository(PAYMENTS_FILE))
    except PaymentStorageError as error:
        print(f"Ошибка хранилища: {error}")


if __name__ == "__main__":
    main()
