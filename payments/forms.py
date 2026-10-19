from datetime import date

from django import forms

from payment_tracking_system.models import PaymentMethod, PaymentStatus


class PaymentSearchForm(forms.Form):
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
    payer_id = forms.TypedChoiceField(
        label="Плательщик",
        required=False,
        coerce=int,
        empty_value=None,
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
    page = forms.IntegerField(
        required=False, min_value=1, max_value=1_000_000, widget=forms.HiddenInput
    )

    def __init__(self, *args, payers=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["payer_id"].choices = [("", "Все плательщики")] + [
            (row["id"], row["full_name"]) for row in payers
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
