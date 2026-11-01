from django.contrib import admin

from .models import ProcessedUpdate


@admin.register(ProcessedUpdate)
class ProcessedUpdateAdmin(admin.ModelAdmin):
    list_display = ("update_id", "processed_at")
