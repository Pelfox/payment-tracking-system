from django.conf import settings
from django.db import DatabaseError
from django.utils.functional import SimpleLazyObject

from .models import Invoice


def _invoice_count():
    try:
        return Invoice.objects.count()
    except DatabaseError:
        return None


def payment_context(request):
    return {
        "site_name": settings.SITE_NAME,
        "navigation_invoice_count": SimpleLazyObject(_invoice_count),
    }
