from django.contrib import admin

from .models import Delivery, Subscription


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = (
        "short_token",
        "chat_id",
        "frequency",
        "paused",
        "remote_only",
        "include_keywords",
        "categories",
        "last_digest_at",
        "created_at",
    )
    list_filter = ("frequency", "paused", "remote_only")
    search_fields = ("token", "chat_id")
    readonly_fields = ("token", "linked_at", "last_check_at", "last_digest_at", "created_at")

    @admin.display(description="token")
    def short_token(self, obj: Subscription) -> str:
        return f"{obj.token[:8]}…"


@admin.register(Delivery)
class DeliveryAdmin(admin.ModelAdmin):
    list_display = ("vacancy", "subscription", "sent_at")
    list_filter = ("sent_at",)
    search_fields = ("vacancy__title", "subscription__token")
    list_select_related = ("vacancy", "subscription")
    raw_id_fields = ("vacancy", "subscription")
