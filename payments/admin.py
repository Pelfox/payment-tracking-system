import re
from uuid import UUID

from django.contrib import admin, messages
from django.db import transaction
from django.db.models import Count, Q
from django.urls import reverse
from django.utils.html import format_html, format_html_join

from .models import Invoice, Payer, Payment

admin.site.site_header = "Учёт платежей - администрирование"
admin.site.site_title = "Учёт платежей"
admin.site.index_title = "Управление данными"


class UnicodeSearchAdmin(admin.ModelAdmin):
    def get_search_results(self, request, queryset, search_term):
        if not search_term:
            return queryset, False
        conditions = Q()
        for field in self.search_fields:
            if field == "=id":
                try:
                    payment_id = UUID(search_term)
                except ValueError:
                    continue
                conditions |= Q(pk=payment_id)
            else:
                conditions |= Q(**{f"{field}__iregex": re.escape(search_term)})
        return queryset.filter(conditions), False


class ReadOnlyInline(admin.TabularInline):
    extra = 0
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class InvoiceInline(ReadOnlyInline):
    model = Invoice
    fields = ("account_number", "amount_cents")
    readonly_fields = fields
    ordering = ("account_number", "pk")


class PaymentInline(ReadOnlyInline):
    model = Payment
    fields = ("id", "paid_at", "amount_cents", "method", "status")
    readonly_fields = fields
    ordering = ("-paid_at", "pk")


class CommissionFilter(admin.SimpleListFilter):
    title = "Есть комиссия"
    parameter_name = "has_commission__exact"

    def lookups(self, request, model_admin):
        return (("1", "Да"), ("0", "Нет"))

    def queryset(self, request, queryset):
        if self.value() in ("0", "1"):
            return queryset.filter(has_commission=self.value() == "1")
        return queryset


@admin.register(Payer)
class PayerAdmin(UnicodeSearchAdmin):
    list_display = ("id", "full_name", "invoice_count", "payment_count")
    list_display_links = ("id",)
    list_editable = ("full_name",)
    search_fields = ("full_name",)
    ordering = ("full_name", "pk")
    list_per_page = 20
    readonly_fields = ("id",)
    inlines = (InvoiceInline,)

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(
                invoice_total=Count("invoices", distinct=True),
                payment_total=Count("invoices__payments"),
            )
        )

    @admin.display(description="Счетов", ordering="invoice_total")
    def invoice_count(self, obj):
        return obj.invoice_total

    @admin.display(description="Платежей", ordering="payment_total")
    def payment_count(self, obj):
        return obj.payment_total

    def get_readonly_fields(self, request, obj=None):
        if request.user.has_perm("payments.view_payment"):
            return (*self.readonly_fields, "payment_history")
        return self.readonly_fields

    @admin.display(description="Последние 20 платежей")
    def payment_history(self, obj):
        if obj is None:
            return "Сначала сохраните плательщика."
        rows = Payment.objects.with_related().filter(invoice__payer=obj)[:20]
        links = format_html_join(
            "",
            '<li><a href="{}">{} · {} · {} коп. · {}</a></li>',
            (
                (
                    reverse("admin:payments_payment_change", args=[row.pk]),
                    row.invoice.account_number,
                    row.paid_at,
                    row.amount_cents,
                    row.get_status_display(),
                )
                for row in rows
            ),
        )
        all_url = reverse("admin:payments_payment_changelist")
        return format_html(
            '<ul>{}</ul><a href="{}?invoice__payer__id__exact={}">Все платежи</a>',
            links,
            all_url,
            obj.pk,
        )


@admin.register(Invoice)
class InvoiceAdmin(UnicodeSearchAdmin):
    list_display = ("id", "account_number", "payer", "amount_cents")
    list_display_links = ("id", "account_number")
    search_fields = ("account_number", "payer__full_name")
    list_filter = (("payer", admin.RelatedOnlyFieldListFilter),)
    ordering = ("account_number", "pk")
    list_per_page = 20
    list_select_related = ("payer",)
    autocomplete_fields = ("payer",)
    readonly_fields = ("id", "slug")
    inlines = (PaymentInline,)

    def get_readonly_fields(self, request, obj=None):
        # Счёт хранит снимок условий операции. Его изменение исказило бы историю.
        if obj is not None and obj.payments.exists():
            return (*self.readonly_fields, "payer", "account_number", "amount_cents")
        return self.readonly_fields


@admin.register(Payment)
class PaymentAdmin(UnicodeSearchAdmin):
    list_display = (
        "id",
        "payer_name",
        "invoice",
        "amount_cents",
        "method",
        "paid_at",
        "status",
        "cancelled_at",
        "commission_present",
    )
    list_display_links = ("id", "payer_name")
    search_fields = ("=id", "invoice__account_number", "invoice__payer__full_name")
    list_filter = (
        "status",
        "cancelled_at",
        "method",
        CommissionFilter,
        "paid_at",
        ("invoice__payer", admin.RelatedOnlyFieldListFilter),
    )
    ordering = ("-paid_at", "id")
    list_per_page = 20
    list_select_related = ("invoice__payer",)
    autocomplete_fields = ("invoice",)
    date_hierarchy = "paid_at"
    readonly_fields = (
        "id",
        "status",
        "commission_cents",
        "total_charged_cents",
        "remaining_cents",
        "overpayment_cents",
        "commission_present",
        "cancelled_at",
    )
    fieldsets = (
        (
            "Операция",
            {
                "fields": (
                    "id",
                    "invoice",
                    "amount_cents",
                    "method",
                    "paid_at",
                    "receipt",
                )
            },
        ),
        ("Результат расчёта", {"fields": readonly_fields[1:]}),
    )
    actions = ("recalculate_payments",)

    @admin.display(description="Плательщик", ordering="invoice__payer__full_name")
    def payer_name(self, obj):
        return obj.invoice.payer.full_name

    @admin.display(description="Есть комиссия", boolean=True, ordering="has_commission")
    def commission_present(self, obj):
        return obj.has_commission

    @admin.action(
        description="Пересчитать итоги выбранных платежей", permissions=["change"]
    )
    def recalculate_payments(self, request, queryset):
        with transaction.atomic():
            count = 0
            for payment in queryset.with_related().select_for_update():
                payment.save()
                count += 1
        self.message_user(request, f"Пересчитано платежей: {count}.", messages.SUCCESS)
