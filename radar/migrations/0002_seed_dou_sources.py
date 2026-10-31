from urllib.parse import quote

from django.db import migrations

DOU_FEED = "https://jobs.dou.ua/vacancies/feeds/?category={}"
DOU_CATEGORIES = ["Front End", "JavaScript", "Python", "Node.js"]


def seed_sources(apps, schema_editor):
    Source = apps.get_model("radar", "Source")
    for category in DOU_CATEGORIES:
        Source.objects.get_or_create(
            feed_url=DOU_FEED.format(quote(category)),
            defaults={"name": f"DOU: {category}", "category": category},
        )


def unseed_sources(apps, schema_editor):
    Source = apps.get_model("radar", "Source")
    Source.objects.filter(
        feed_url__in=[DOU_FEED.format(quote(c)) for c in DOU_CATEGORIES],
        vacancies__isnull=True,
    ).delete()


class Migration(migrations.Migration):
    dependencies = [("radar", "0001_initial")]

    operations = [migrations.RunPython(seed_sources, unseed_sources)]
