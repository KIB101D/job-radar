from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import JobLock

FETCH_LOCK = "fetch_vacancies"

_EPOCH = datetime(1970, 1, 1, tzinfo=dt_timezone.utc)


def acquire(name: str, ttl: timedelta) -> bool:
    """Try to take the lock. Returns False immediately if someone else holds it."""
    now = timezone.now()
    try:
        with transaction.atomic():
            JobLock.objects.get_or_create(name=name, defaults={"locked_until": _EPOCH})
    except IntegrityError:
        pass  # another process created the row first; that's fine
    # The conditional UPDATE is the actual lock: only one caller can flip an
    # expired row, because the database serialises writes to that row.
    updated = JobLock.objects.filter(name=name, locked_until__lt=now).update(
        locked_until=now + ttl
    )
    return updated == 1


def release(name: str) -> None:
    JobLock.objects.filter(name=name).update(locked_until=_EPOCH)


@contextmanager
def job_lock(name: str, ttl: timedelta) -> Iterator[bool]:
    """
    Usage:
        with job_lock(FETCH_LOCK, timedelta(minutes=10)) as acquired:
            if not acquired:
                return
            ...
    """
    acquired = acquire(name, ttl)
    try:
        yield acquired
    finally:
        if acquired:
            release(name)
