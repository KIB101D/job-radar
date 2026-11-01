"""More DOU categories, each with its own RSS feed (names as on jobs.dou.ua)."""

from urllib.parse import quote

from django.db import migrations

DOU_FEED = "https://jobs.dou.ua/vacancies/feeds/?category={}"
NEW_CATEGORIES = [
    "QA",
    "DevOps",
    "Data Science",
    "Data Engineer",
    "AI/ML",
    "Java",
    ".NET",
    "PHP",
    "Golang",
    "C++",
    "Android",
    "iOS/macOS",
    "Flutter",
    "React Native",
]


def feed_url(category: str) -> str:
    return DOU_FEED.format(quote(category, safe=""))  # "C++" -> C%2B%2B, "AI/ML" -> AI%2FML


def add_sources(apps, schema_editor):
    Source = apps.get_model("radar", "Source")
    for category in NEW_CATEGORIES:
        Source.objects.get_or_create(
            feed_url=feed_url(category),
            defaults={"name": f"DOU: {category}", "category": category},
        )


def remove_sources(apps, schema_editor):
    Source = apps.get_model("radar", "Source")
    Source.objects.filter(
        feed_url__in=[feed_url(c) for c in NEW_CATEGORIES],
        vacancies__isnull=True,
        listed_vacancies__isnull=True,
    ).delete()


class Migration(migrations.Migration):
    dependencies = [("radar", "0006_vacancy_last_checked_at")]

    operations = [migrations.RunPython(add_sources, remove_sources)]
