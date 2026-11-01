import hmac
import json
import logging

from django.conf import settings
from django.db import IntegrityError, transaction
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .handlers import safe_handle_update
from .models import ProcessedUpdate

logger = logging.getLogger(__name__)

SECRET_HEADER = "HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN"


@csrf_exempt  # Telegram can't send a CSRF token; the secret header replaces it.
@require_POST
def webhook(request):
    """
    Telegram calls this with every update. It must answer 200 quickly: on errors
    or timeouts Telegram retries the same update, so updates are de-duplicated
    by update_id and handler errors are logged instead of returned as 500.
    """
    expected = settings.TELEGRAM_WEBHOOK_SECRET
    received = request.META.get(SECRET_HEADER, "")
    if not expected or not hmac.compare_digest(received.encode(), expected.encode()):
        return HttpResponseForbidden()

    try:
        update = json.loads(request.body)
        update_id = int(update["update_id"])
    except (ValueError, KeyError, TypeError):
        return HttpResponseBadRequest()

    try:
        with transaction.atomic():  # a savepoint, so a duplicate doesn't poison the request
            ProcessedUpdate.objects.create(update_id=update_id)
    except IntegrityError:
        logger.info("Update %s already processed, skipping", update_id)
        return HttpResponse()

    safe_handle_update(update)
    return HttpResponse()
