from django.db import models


class ProcessedUpdate(models.Model):
    """
    Telegram update ids we have already handled. Telegram re-sends an update if
    our webhook answers slowly or with an error, so without this a retried
    "Перевірити зараз" could send the same vacancies twice.
    """

    update_id = models.BigIntegerField(primary_key=True)
    processed_at = models.DateTimeField(auto_now_add=True, db_index=True)

    def __str__(self) -> str:
        return str(self.update_id)

    # Telegram only re-sends an update for about a day; a week of ids is plenty.
    KEEP_FOR_DAYS = 7

    @classmethod
    def prune(cls) -> int:
        from datetime import timedelta

        from django.utils import timezone

        cutoff = timezone.now() - timedelta(days=cls.KEEP_FOR_DAYS)
        deleted, _ = cls.objects.filter(processed_at__lt=cutoff).delete()
        return deleted


class ChatState(models.Model):
    """
    Bot UI state for one chat (not a domain concept, so not on Subscription).

    The settings panel is ONE message that is edited in place as you move between
    its screens. We remember which message it is and which screen it shows, so
    reopening the panel can replace it and the website can update it.
    """

    chat_id = models.BigIntegerField(primary_key=True)
    panel_message_id = models.BigIntegerField(null=True, blank=True)
    panel_screen = models.CharField(max_length=20, default="main")
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"chat {self.chat_id}: {self.panel_screen}"
