from django.conf import settings
from django.contrib import admin
from django.urls import include, path

handler404 = "homepage.views.page_not_found"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("homepage.urls")),
    path("payments/", include("payments.urls")),
]

if settings.DEBUG_TOOLBAR_ENABLED:
    urlpatterns += [path("__debug__/", include("debug_toolbar.urls"))]
