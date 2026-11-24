from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.generic import TemplateView


class IndexView(TemplateView):
    template_name = "homepage/index.html"

    def head(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)


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
