from django.db import models


def _upload_path(instance, filename):
    return f"plaud/{filename}"


class PlaudRecording(models.Model):
    plaud_id = models.CharField(max_length=64, unique=True, blank=True, default="")
    file = models.FileField(upload_to=_upload_path)
    original_name = models.CharField(max_length=255, blank=True)
    size_bytes = models.BigIntegerField(default=0)
    duration_ms = models.BigIntegerField(default=0)
    serial_number = models.CharField(max_length=64, blank=True)
    transcription = models.TextField(blank=True)
    title = models.CharField(max_length=255, blank=True)
    summary = models.TextField(blank=True)
    processed = models.BooleanField(default=False)
    summarized = models.BooleanField(default=False)
    error = models.TextField(blank=True)
    recorded_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-recorded_at", "-created_at"]

    def __str__(self):
        return self.original_name or self.file.name
