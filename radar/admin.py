from django.contrib import admin

from .models import JobLock, Source, Vacancy


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "is_enabled", "last_fetched_at", "has_error")
    list_filter = ("is_enabled", "category")
    search_fields = ("name", "feed_url")
    list_editable = ("is_enabled",)

    @admin.display(boolean=True, description="error")
    def has_error(self, obj: Source) -> bool:
        return bool(obj.last_error)


@admin.register(Vacancy)
class VacancyAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "company",
        "source",
        "location",
        "is_remote",
        "salary_text",
        "published_at",
        "first_seen_at",
        "is_active",
    )
    list_filter = ("is_active", "is_remote", "source")
    search_fields = ("title", "company", "description_text", "guid")
    date_hierarchy = "published_at"
    list_select_related = ("source",)
    readonly_fields = ("guid", "first_seen_at", "last_seen_at")
    list_per_page = 50


@admin.register(JobLock)
class JobLockAdmin(admin.ModelAdmin):
    list_display = ("name", "locked_until")
