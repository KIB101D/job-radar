from django.db import models
from django.utils import timezone


class Source(models.Model):
    """A feed we pull vacancies from. Today: DOU RSS by category."""

    name = models.CharField(max_length=100, unique=True)
    feed_url = models.URLField(max_length=500, unique=True)
    # Category label as shown to users and stored in Subscription.categories.
    category = models.CharField(max_length=50, db_index=True)
    is_enabled = models.BooleanField(default=True)
    last_fetched_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class VacancyQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)

    def newest_first(self):
        return self.order_by("-published_at", "-id")

    def in_categories(self, categories):
        return self.filter(sources__category__in=list(categories)).distinct()


class Vacancy(models.Model):
    # Canonical vacancy URL without query string. DOU's own <guid> carries a
    # "?<timestamp>" suffix that changes when a vacancy is bumped, so it can't
    # be used for dedupe as-is (see radar/services/parser.py).
    guid = models.CharField(max_length=500, unique=True)
    # The feed where we first saw it. A DOU vacancy can be listed in several
    # categories at once (e.g. Front End + JavaScript), so `sources` keeps all of
    # them and category filtering uses that.
    source = models.ForeignKey(Source, on_delete=models.PROTECT, related_name="vacancies")
    sources = models.ManyToManyField(Source, related_name="listed_vacancies", blank=True)
    title = models.CharField(max_length=300)
    company = models.CharField(max_length=200, blank=True)
    url = models.URLField(max_length=500)
    description_text = models.TextField(blank=True)
    location = models.CharField(max_length=300, blank=True)
    is_remote = models.BooleanField(default=False, db_index=True)
    salary_text = models.CharField(max_length=100, blank=True)
    published_at = models.DateTimeField(db_index=True)
    first_seen_at = models.DateTimeField(default=timezone.now, db_index=True)
    last_seen_at = models.DateTimeField()
    is_active = models.BooleanField(default=True, db_index=True)

    objects = VacancyQuerySet.as_manager()

    class Meta:
        ordering = ["-published_at", "-id"]
        verbose_name_plural = "vacancies"

    def __str__(self) -> str:
        return f"{self.title} @ {self.company}" if self.company else self.title

    @property
    def categories(self) -> list[str]:
        """Category labels of every feed listing this vacancy (prefetch `sources`)."""
        return sorted({s.category for s in self.sources.all()})


class JobLock(models.Model):
    """
    Tiny DB-backed lock so that two fetches never run at the same time
    (cron run vs. a "check now" press from the bot, or two presses at once).

    Acquired with one atomic conditional UPDATE, so it works the same on
    SQLite and Postgres and needs no Redis. A lock expires on its own after
    its TTL, so a crashed job can't block fetching forever.
    """

    name = models.CharField(max_length=50, primary_key=True)
    locked_until = models.DateTimeField()

    def __str__(self) -> str:
        return f"{self.name} (until {self.locked_until:%Y-%m-%d %H:%M:%S})"
