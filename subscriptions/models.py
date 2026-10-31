import secrets

from django.db import models


def generate_token() -> str:
    # ~32 chars, URL-safe; fits Telegram's 64-char /start payload limit.
    return secrets.token_urlsafe(24)


class Frequency(models.TextChoices):
    INSTANT = "instant", "Щойно з'являються"
    EVERY_3H = "every_3h", "Кожні 3 год"
    TWICE_DAILY_9_18 = "twice_daily_9_18", "2 рази на день: 9:00 і 18:00"
    DAILY_9 = "daily_9", "Раз на день о 9:00"
    OFF = "off", "Не надсилати"


class Subscription(models.Model):
    """
    One set of filters shared by the website and the Telegram bot.
    The token is the only "credential": whoever has /s/<token>/ can edit it,
    and /start <token> links a Telegram chat to it.
    """

    token = models.CharField(max_length=64, unique=True, default=generate_token, editable=False)
    chat_id = models.BigIntegerField(null=True, blank=True, unique=True)

    # Filters. Stored as lists of strings; the site form accepts comma-separated input.
    include_keywords = models.JSONField(default=list, blank=True)
    exclude_keywords = models.JSONField(default=list, blank=True)
    categories = models.JSONField(default=list, blank=True)  # Source.category values
    remote_only = models.BooleanField(default=False)

    # Delivery settings.
    frequency = models.CharField(
        max_length=20, choices=Frequency.choices, default=Frequency.DAILY_9
    )
    timezone = models.CharField(max_length=50, default="Europe/Kyiv")
    quiet_hours_start = models.PositiveSmallIntegerField(default=22)
    quiet_hours_end = models.PositiveSmallIntegerField(default=8)
    paused = models.BooleanField(default=False)

    # Bookkeeping.
    linked_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When a Telegram chat was linked. Digests only include vacancies "
        "first seen after this moment, so the first digest isn't a dump of old ones.",
    )
    last_check_at = models.DateTimeField(null=True, blank=True)  # "check now" rate limit
    last_digest_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        who = f"chat {self.chat_id}" if self.chat_id else "no chat"
        return f"Subscription {self.token[:8]}… ({who})"

    @property
    def is_linked(self) -> bool:
        return self.chat_id is not None


class Delivery(models.Model):
    """A vacancy that was sent to a subscription. Unique pair = never sent twice."""

    subscription = models.ForeignKey(
        Subscription, on_delete=models.CASCADE, related_name="deliveries"
    )
    vacancy = models.ForeignKey(
        "radar.Vacancy", on_delete=models.CASCADE, related_name="deliveries"
    )
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["subscription", "vacancy"], name="unique_delivery_per_subscription"
            )
        ]
        ordering = ["-sent_at"]
        verbose_name_plural = "deliveries"

    def __str__(self) -> str:
        return f"{self.vacancy_id} → {self.subscription_id}"
