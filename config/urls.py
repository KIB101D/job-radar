from django.contrib import admin
from django.http import HttpResponse
from django.urls import include, path

from bot.views import webhook


def healthz(request):
    """Render's health check: the process answers. No DB, so Neon can stay asleep."""
    return HttpResponse("ok", content_type="text/plain")


urlpatterns = [
    path("healthz/", healthz, name="healthz"),
    path("admin/", admin.site.urls),
    path("telegram/webhook/", webhook, name="telegram_webhook"),
    path("", include("subscriptions.urls")),
]
