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
