from django.db import migrations

# Phase 2 frequencies -> clock-aligned frequencies from the Figma design.
OLD_TO_NEW = {
    "instant": "hourly",
    "twice_daily_9_18": "every_12h",
    "daily_9": "daily",
}


def forwards(apps, schema_editor):
    Subscription = apps.get_model("subscriptions", "Subscription")
    for old, new in OLD_TO_NEW.items():
        Subscription.objects.filter(frequency=old).update(frequency=new)


class Migration(migrations.Migration):
    dependencies = [("subscriptions", "0002_clock_aligned_frequencies")]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
