# Django использует словари Meta как декларативную конфигурацию форм.
# ruff: noqa: RUF012
from datetime import date

from django import forms
from django.utils import timezone

from payment_tracking_system.models import PaymentMethod, PaymentStatus

from .models import Invoice, Payer, Payment

PAYMENT_SORT_FIELDS = {
    "date": ("-paid_at", "id"),
    "date_asc": ("paid_at", "id"),
    "amount": ("amount_cents", "id"),
    "amount_desc": ("-amount_cents", "id"),
    "payer": ("invoice__payer__full_name", "-paid_at", "id"),
    "invoice": ("invoice__account_number", "-paid_at", "id"),
}


class PaymentSearchForm(forms.Form):
    lifecycle = forms.ChoiceField(
        label="Состояние записи",
        required=False,
        choices=[
            ("", "Действующие"),
            ("cancelled", "Отменённые"),
            ("all", "Все записи"),
        ],
    )
    q = forms.CharField(
        label="Поиск по имени или номеру счёта", required=False, max_length=200
    )
    payer_name = forms.CharField(
        label="ФИО плательщика содержит", required=False, max_length=200
    )
    status = forms.ChoiceField(
        label="Статус",
        required=False,
        choices=[("", "Все статусы")] + [(s.value, s.value) for s in PaymentStatus],
    )
    method = forms.ChoiceField(
        label="Способ оплаты",
        required=False,
        choices=[("", "Любой способ")] + [(m.value, m.value) for m in PaymentMethod],
    )
    exclude_status = forms.ChoiceField(
        label="Исключить статус",
        required=False,
        choices=[("", "Не исключать")] + [(s.value, s.value) for s in PaymentStatus],
    )
    payer_id = forms.TypedChoiceField(
        label="Плательщик",
        required=False,
        coerce=int,
        empty_value=None,
    )
    invoice_id = forms.TypedChoiceField(
        label="Счёт", required=False, coerce=int, empty_value=None
    )
    date_from = forms.DateField(
        label="Дата с",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    date_to = forms.DateField(
        label="Дата по (включительно)",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    min_amount = forms.DecimalField(
        label="Сумма от, руб.",
        required=False,
        min_value=0,
        max_digits=12,
        decimal_places=2,
    )
    sort = forms.ChoiceField(
        label="Сортировка",
        required=False,
        choices=[
            ("date", "Сначала новые"),
            ("date_asc", "Сначала старые"),
            ("amount", "Сумма по возрастанию"),
            ("amount_desc", "Сумма по убыванию"),
            ("payer", "По имени плательщика"),
            ("invoice", "По номеру счёта"),
        ],
    )

    def __init__(self, *args, payers=(), invoices=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["payer_id"].choices = [("", "Все плательщики")] + [
            (row["id"], row["full_name"]) for row in payers
        ]
        self.fields["invoice_id"].choices = [("", "Все счета")] + [
            (
                row["id"],
                f"{row['account_number']} - {row['payer__full_name']}",
            )
            for row in invoices
        ]
        for field in self.fields.values():
            field.widget.attrs["class"] = (
                "form-select"
                if isinstance(field.widget, forms.Select)
                else "form-control"
            )

    def clean(self):
        data = super().clean()
        start, end = data.get("date_from"), data.get("date_to")
        if start and end and start > end:
            raise forms.ValidationError("Начальная дата позже конечной.")
        if start == date.min:
            self.add_error("date_from", "Выберите дату после 01.01.0001.")
        if end == date.max:
            self.add_error("date_to", "Выберите дату до 31.12.9999.")
        return data


class PayerStatisticsForm(forms.Form):
    min_count = forms.IntegerField(
        label="Показать плательщиков с числом операций больше",
        required=False,
        min_value=0,
        max_value=1_000_000,
        widget=forms.NumberInput(attrs={"class": "form-control"}),
    )


class StyledModelForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = (
                "form-select"
                if isinstance(field.widget, forms.Select)
                else "form-control"
            )


class PayerForm(StyledModelForm):
    class Meta:
        model = Payer
        fields = ("full_name",)
        widgets = {"full_name": forms.TextInput(attrs={"placeholder": "Иван Иванов"})}

    def clean_full_name(self):
        return " ".join(self.cleaned_data["full_name"].split())


class InvoiceForm(StyledModelForm):
    class Meta:
        model = Invoice
        fields = ("payer", "account_number", "amount_cents")
        widgets = {
            "account_number": forms.TextInput(attrs={"placeholder": "СЧЁТ-001"}),
        }


class InvoiceChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.account_number} - {obj.payer.full_name} - {obj.amount:.2f} руб."


class PaymentForm(StyledModelForm):
    payer = forms.ModelChoiceField(
        label="Плательщик",
        queryset=Payer.objects.all(),
        help_text="Выберите плательщика, которому принадлежит счёт.",
    )
    invoice = InvoiceChoiceField(
        label="Счёт", queryset=Invoice.objects.select_related("payer")
    )

    class Meta:
        model = Payment
        fields = ("payer", "invoice", "amount_cents", "method", "paid_at", "receipt")
        widgets = {
            "paid_at": forms.DateTimeInput(
                format="%Y-%m-%dT%H:%M:%S",
                attrs={"type": "datetime-local", "step": "1"},
            ),
            "receipt": forms.ClearableFileInput(
                attrs={"accept": "image/png,image/jpeg,image/webp"}
            ),
        }
        help_texts = {
            "paid_at": "Московское время. Можно указать прошедшую дату платежа.",
            "receipt": "PNG, JPEG или WebP, не более 5 МБ. Поле необязательно.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance._state.adding:
            self.initial["payer"] = self.instance.invoice.payer_id

    def clean_paid_at(self):
        paid_at = self.cleaned_data["paid_at"]
        if paid_at > timezone.now():
            raise forms.ValidationError("Дата платежа не может быть в будущем.")
        return paid_at

    def clean_receipt(self):
        receipt = self.cleaned_data.get("receipt")
        if receipt and hasattr(receipt, "image"):
            if receipt.size > 5 * 1024 * 1024:
                raise forms.ValidationError(
                    "Размер квитанции не должен превышать 5 МБ."
                )
            if receipt.image.format not in {"PNG", "JPEG", "WEBP"}:
                raise forms.ValidationError("Выберите изображение PNG, JPEG или WebP.")
        return receipt

    def clean(self):
        data = super().clean()
        payer, invoice = data.get("payer"), data.get("invoice")
        if payer and invoice and invoice.payer_id != payer.pk:
            raise forms.ValidationError(
                "Выбранный счёт принадлежит другому плательщику."
            )
        return data
