import datetime

def print_payment(
    payer: str,
    account_number: str,
    bill_amount: float,
    payment_amount: float,
    payment_method: str,
) -> None:
    payment_date = datetime.datetime.now()
    if payment_amount <= 0:
        print("\nОшибка: сумма платежа должна быть больше нуля.")

    else:
        if payment_method == "карта":
            commission = payment_amount * 0.01
        else:
            commission = 0

        total_payment = payment_amount + commission
        difference = bill_amount - payment_amount

        print("\n=== Информация о платеже ===")
        print("Плательщик:", payer)
        print("Номер счёта:", account_number)
        print("Сумма счёта:", bill_amount, "руб.")
        print("Сумма платежа:", payment_amount, "руб.")
        print("Комиссия:", commission, "руб.")
        print("Итого списано:", total_payment, "руб.")
        print("Дата платежа:", payment_date)

        if payment_amount == bill_amount:
            print("Статус: счёт оплачен полностью.")

        elif payment_amount < bill_amount:
            print("Статус: частичная оплата.")
            print("Осталось оплатить:", difference, "руб.")

        else:
            overpayment = payment_amount - bill_amount
            print("Статус: переплата.")
            print("Сумма переплаты:", overpayment, "руб.")


def main() -> None:
    print("=== Система учёта платежей ===")

    payer = input("Введите ФИО плательщика: ")
    account_number = input("Введите номер счёта: ")

    bill_amount = float(input("Введите сумму счёта: "))
    payment_amount = float(input("Введите сумму платежа: "))
    payment_method = input("Способ оплаты (карта/наличные): ")

    print_payment(payer, account_number, bill_amount, payment_amount, payment_method)


if __name__ == "__main__":
    main()
