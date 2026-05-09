from django.contrib import admin

from .models import PlaudRecording


@admin.register(PlaudRecording)
class PlaudRecordingAdmin(admin.ModelAdmin):
    list_display = ("original_name", "processed", "recorded_at", "created_at")
    list_filter = ("processed",)
    search_fields = ("original_name", "transcription")
