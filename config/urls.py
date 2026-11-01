from django.contrib import admin
from django.urls import include, path

from bot.views import webhook

urlpatterns = [
    path("admin/", admin.site.urls),
    path("telegram/webhook/", webhook, name="telegram_webhook"),
    path("", include("subscriptions.urls")),
]
