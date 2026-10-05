from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_safe


@require_safe
def index(request: HttpRequest) -> HttpResponse:
    return render(request, "homepage/index.html")


def page_not_found(request: HttpRequest, exception: Exception) -> HttpResponse:
    return render(
        request,
        "error.html",
        {
            "title": "Страница не найдена",
            "message": "Проверьте адрес или вернитесь на главную страницу.",
            "status_code": 404,
        },
        status=404,
    )
