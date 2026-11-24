import logging
import re
from datetime import datetime, time, timedelta
from functools import wraps
from uuid import UUID

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import Avg, Count, Max, Min, Q, Sum
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.http import (
    require_http_methods,
    require_POST,
    require_safe,
)
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from payment_tracking_system.models import PaymentStatus

from .forms import (
    PAYMENT_SORT_FIELDS,
    InvoiceForm,
    PayerForm,
    PayerStatisticsForm,
    PaymentForm,
    PaymentSearchForm,
)
from .models import Invoice, Payer, Payment, rubles

logger = logging.getLogger(__name__)
PAGE_SIZE = 50


def database_page(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        except DatabaseError:
            logger.exception("Не удалось получить данные платежей через ORM.")
            return render(
                request,
                "error.html",
                {
                    "title": "Платежи временно недоступны",
                    "message": "Не удалось загрузить данные. Попробуйте позже.",
                    "status_code": 503,
                },
                status=503,
            )

    return wrapped


class DataMixin:
    """Общие заголовки, пагинация и контекст для class-based views."""

    title = ""
    heading = ""
    paginate_by = PAGE_SIZE
    back_url = ""
    editing = False
    http_method_names = ("get", "head")

    def get_title(self):
        return self.title

    def get_heading(self):
        return self.heading or self.get_title()

    def get_back_url(self):
        return self.back_url

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault("title", self.get_title())
        context.setdefault("page_title", self.get_heading())
        context.setdefault("back_url", self.get_back_url())
        context.setdefault("editing", self.editing)
        return context

    def get_paginate_by(self, queryset):
        # Размер страницы читается при каждом запросе, а не фиксируется
        # в атрибуте класса.
        return PAGE_SIZE if self.paginate_by is not None else None

    def paginate_queryset(self, queryset, page_size):
        # get_page обрабатывает нечисловые и выходящие за диапазон номера
        # страниц без 404, как и прежняя реализация на Paginator.
        paginator = self.get_paginator(
            queryset,
            page_size,
            orphans=self.get_paginate_orphans(),
            allow_empty_first_page=self.get_allow_empty(),
        )
        page = paginator.get_page(
            self.kwargs.get(self.page_kwarg)
            or self.request.GET.get(self.page_kwarg)
            or 1
        )
        return paginator, page, page.object_list, page.has_other_pages()

    @method_decorator(database_page)
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)

    def head(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)


class PaymentListMixin(DataMixin):
    model = Payment
    context_object_name = "payments"
    template_name = "payments/list.html"
    partial_only = False
    search_only = False

    def get_search_form(self):
        payers = Payer.objects.order_by("full_name", "pk").values("id", "full_name")
        invoices = Invoice.objects.values("id", "account_number", "payer__full_name")
        return PaymentSearchForm(
            self.request.GET if self.request.GET or not self.search_only else None,
            payers=payers,
            invoices=invoices,
        )

    def get_queryset(self):
        self.search_form = self.get_search_form()
        self.search_is_valid = (
            self.search_form.is_bound and self.search_form.is_valid()
        )
        if not self.search_is_valid:
            return Payment.objects.none()

        data = self.search_form.cleaned_data
        payments = Payment.objects.with_related()
        if self.partial_only:
            payments = payments.partially_paid()
        if data["lifecycle"] == "cancelled":
            payments = payments.cancelled()
        elif data["lifecycle"] != "all":
            payments = payments.active()
        conditions = {key: data[key] for key in ("status", "method") if data[key]}
        payments = payments.filter(**conditions)
        if data["payer_id"] is not None:
            payments = payments.filter(invoice__payer_id=data["payer_id"])
        if data["invoice_id"] is not None:
            payments = payments.filter(invoice_id=data["invoice_id"])
        if data["min_amount"] is not None:
            payments = payments.filter(amount_cents__gte=int(data["min_amount"] * 100))
        if data["payer_name"]:
            payments = payments.filter(
                invoice__payer__full_name__iregex=re.escape(data["payer_name"])
            )
        if data["q"]:
            payments = payments.search(data["q"])
        if data["exclude_status"]:
            payments = payments.exclude(status=data["exclude_status"])
        if data["date_from"]:
            start = timezone.make_aware(datetime.combine(data["date_from"], time.min))
            payments = payments.filter(paid_at__gte=start)
        if data["date_to"]:
            end = timezone.make_aware(
                datetime.combine(data["date_to"] + timedelta(days=1), time.min)
            )
            payments = payments.filter(paid_at__lt=end)
        return payments.order_by(*PAYMENT_SORT_FIELDS[data["sort"] or "date"])

    def get_paginate_by(self, queryset):
        # При ошибках поиска выводится форма, а не список результатов.
        if not self.search_is_valid:
            return None
        return super().get_paginate_by(queryset)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "form": self.search_form,
                "statuses": list(PaymentStatus),
                "partial_only": self.partial_only,
                "searched": self.search_form.is_bound,
            }
        )
        return context

    def render_to_response(self, context, **response_kwargs):
        if self.search_form.is_bound and not self.search_is_valid:
            response_kwargs["status"] = 400
        return super().render_to_response(context, **response_kwargs)


class PaymentListView(PaymentListMixin, ListView):
    title = "Платежи"


class PartialPaymentListView(PaymentListView):
    title = "Частичная оплата"
    partial_only = True


class PaymentSearchView(PaymentListView):
    title = "Поиск платежей"
    template_name = "payments/search.html"
    search_only = True


class PaymentDetailView(DataMixin, DetailView):
    model = Payment
    template_name = "payments/detail.html"
    context_object_name = "payment"
    pk_url_kwarg = "payment_id"
    heading = "Информация о платеже"

    def get_queryset(self):
        return Payment.objects.with_related()

    def get_title(self):
        return f"Платёж {self.object.id}"

    def get(self, request, *args, **kwargs):
        try:
            return super().get(request, *args, **kwargs)
        except Http404:
            return render(
                request,
                "error.html",
                {
                    "title": "Платёж не найден",
                    "message": (
                        "Платежа с таким идентификатором нет среди "
                        "сохранённых операций."
                    ),
                    "status_code": 404,
                    "back_to_payments": True,
                },
                status=404,
            )


class PaymentFormMixin(DataMixin):
    model = Payment
    form_class = PaymentForm
    template_name = "payments/form.html"
    back_url = reverse_lazy("payments:list")
    http_method_names = ("get", "post")

    def form_invalid(self, form):
        return self.render_to_response(self.get_context_data(form=form), status=400)

    def form_valid(self, form):
        try:
            with transaction.atomic():
                self.object = form.save()
        except ValidationError as error:
            form.add_error(None, error.messages)
            return self.form_invalid(form)
        except IntegrityError:
            form.add_error(
                None, "Такая запись уже существует. Проверьте введённые данные."
            )
            return self.form_invalid(form)
        messages.success(
            self.request,
            "Изменения сохранены." if self.editing else "Запись создана.",
        )
        return redirect(self.get_success_url())


class PaymentCreateView(PaymentFormMixin, CreateView):
    title = "Новый платёж"


class PaymentUpdateView(PaymentFormMixin, UpdateView):
    title = "Редактирование платежа"
    editing = True
    pk_url_kwarg = "payment_id"

    def get_queryset(self):
        payments = Payment.objects.active().with_related()
        if self.request.method == "POST":
            payments = payments.select_for_update()
        return payments

    def get_back_url(self):
        return self.object.get_absolute_url()

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)


def _model_form_page(request, form_class, *, title, back_url, instance=None):
    form = form_class(
        request.POST if request.method == "POST" else None,
        request.FILES if request.method == "POST" else None,
        instance=instance,
    )
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                obj = form.save()
        except ValidationError as error:
            form.add_error(None, error.messages)
        except IntegrityError:
            form.add_error(
                None, "Такая запись уже существует. Проверьте введённые данные."
            )
        else:
            messages.success(
                request, "Изменения сохранены." if instance else "Запись создана."
            )
            return redirect(
                obj.get_absolute_url() if hasattr(obj, "get_absolute_url") else back_url
            )
    return render(
        request,
        "payments/form.html",
        {
            "form": form,
            "title": title,
            "back_url": back_url,
            "editing": instance is not None,
        },
        status=400 if request.method == "POST" else 200,
    )


@require_POST
@database_page
def payment_cancel(request: HttpRequest, payment_id: UUID) -> HttpResponse:
    payment = get_object_or_404(Payment.objects.active(), pk=payment_id)
    payment.cancel()
    messages.success(request, "Запись отменена и сохранена в истории.")
    return redirect(payment)


@require_http_methods(["GET", "POST"])
@database_page
def payer_create(request: HttpRequest) -> HttpResponse:
    return _model_form_page(
        request,
        PayerForm,
        title="Новый плательщик",
        back_url=reverse("payments:payers"),
    )


@require_http_methods(["GET", "POST"])
@database_page
def invoice_create(request: HttpRequest) -> HttpResponse:
    return _model_form_page(
        request, InvoiceForm, title="Новый счёт", back_url=reverse("payments:invoices")
    )


@require_safe
@database_page
def payer_list(request: HttpRequest) -> HttpResponse:
    payers = Payer.objects.annotate(
        invoice_count=Count("invoices", distinct=True),
        payment_count=Count(
            "invoices__payments",
            filter=Q(invoices__payments__cancelled_at__isnull=True),
        ),
    ).order_by("full_name", "pk")
    return render(request, "payments/payers.html", {"payers": payers})


@require_safe
@database_page
def invoice_list(request: HttpRequest) -> HttpResponse:
    invoices = Invoice.objects.select_related("payer").order_by("account_number", "pk")
    return render(request, "payments/invoices.html", {"invoices": invoices})


@require_safe
@database_page
def invoice_detail(request: HttpRequest, slug: str) -> HttpResponse:
    invoice = get_object_or_404(Invoice.objects.select_related("payer"), slug=slug)
    payments = invoice.payments.with_related()
    page_obj = Paginator(payments, PAGE_SIZE).get_page(request.GET.get("page"))
    return render(
        request,
        "payments/invoice_detail.html",
        {"invoice": invoice, "payments": page_obj.object_list, "page_obj": page_obj},
    )


@require_safe
@database_page
def payment_summary(request: HttpRequest) -> HttpResponse:
    active = Payment.objects.active()
    totals = active.aggregate(
        total_amount_cents=Sum("amount_cents", default=0),
        total_commission_cents=Sum("commission_cents", default=0),
        min_amount_cents=Min("amount_cents", default=0),
        max_amount_cents=Max("amount_cents", default=0),
        avg_amount_cents=Avg("amount_cents", default=0),
    )
    partial_totals = active.partially_paid().aggregate(
        remaining_cents=Sum("remaining_cents", default=0)
    )
    overpaid_totals = active.overpaid().aggregate(
        overpayment_cents=Sum("overpayment_cents", default=0)
    )
    counts = {
        row["status"]: row["payment_count"]
        for row in active.values("status").annotate(payment_count=Count("pk"))
    }
    methods = list(
        active.values("method")
        .annotate(
            payment_count=Count("pk"),
            amount_cents=Sum("amount_cents"),
            commission_cents=Sum("commission_cents"),
        )
        .order_by("method")
    )
    for method in methods:
        method["amount"] = rubles(method["amount_cents"])
        method["commission"] = rubles(method["commission_cents"])
    form = PayerStatisticsForm(request.GET)
    valid = form.is_valid()
    payers = []
    if valid:
        payers = list(
            Payer.objects.annotate(
                payment_count=Count(
                    "invoices__payments",
                    filter=Q(invoices__payments__cancelled_at__isnull=True),
                ),
                amount_cents=Sum(
                    "invoices__payments__amount_cents",
                    filter=Q(invoices__payments__cancelled_at__isnull=True),
                    default=0,
                ),
            )
            .filter(payment_count__gt=form.cleaned_data.get("min_count") or 0)
            .order_by("-payment_count", "full_name", "pk")
        )
        for payer in payers:
            payer.payment_amount = rubles(payer.amount_cents)
    return render(
        request,
        "payments/summary.html",
        {
            "payment_count": active.count(),
            "total_amount": rubles(totals["total_amount_cents"]),
            "total_commission": rubles(totals["total_commission_cents"]),
            "min_amount": rubles(totals["min_amount_cents"]),
            "max_amount": rubles(totals["max_amount_cents"]),
            "avg_amount": rubles(totals["avg_amount_cents"]),
            "total_remaining": rubles(partial_totals["remaining_cents"]),
            "total_overpayment": rubles(overpaid_totals["overpayment_cents"]),
            "invoices_without_payments": Invoice.objects.filter(
                payments__isnull=True
            ).count(),
            "status_counts": [(s, counts.get(s.value, 0)) for s in PaymentStatus],
            "method_totals": methods,
            "payer_statistics": payers,
            "statistics_form": form,
        },
        status=200 if valid else 400,
    )
