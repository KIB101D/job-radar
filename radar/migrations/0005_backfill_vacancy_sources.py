from django.db import migrations


def backfill(apps, schema_editor):
    """Existing vacancies are listed at least in the feed they came from.
    Other feeds get attached on the next fetch_vacancies run."""
    Vacancy = apps.get_model("radar", "Vacancy")
    Through = Vacancy.sources.through
    Through.objects.bulk_create(
        [
            Through(vacancy_id=vacancy_id, source_id=source_id)
            for vacancy_id, source_id in Vacancy.objects.values_list("id", "source_id")
        ],
        ignore_conflicts=True,
    )


class Migration(migrations.Migration):
    dependencies = [("radar", "0004_vacancy_sources")]

    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
