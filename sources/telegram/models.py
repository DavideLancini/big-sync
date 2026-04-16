from django.db import models


class MediaType(models.TextChoices):
    TEXT = "text", "Text"
    PHOTO = "photo", "Photo"
    VIDEO = "video", "Video"
    VOICE = "voice", "Voice"
    AUDIO = "audio", "Audio"
    STICKER = "sticker", "Sticker"
    GIF = "gif", "GIF"
    VIDEO_NOTE = "video_note", "Video Note"
    DOCUMENT = "document", "Document"
    UNKNOWN = "unknown", "Unknown"


class TelegramMessage(models.Model):
    message_id = models.BigIntegerField()
    chat_id = models.BigIntegerField()
    chat_name = models.CharField(max_length=255, blank=True)
    sender_id = models.BigIntegerField(null=True, blank=True)
    sender_name = models.CharField(max_length=255, blank=True)
    text = models.TextField(blank=True)
    media_type = models.CharField(
        max_length=20, choices=MediaType.choices, default=MediaType.TEXT
    )
    media_downloaded = models.BooleanField(default=False)
    media_path = models.CharField(max_length=500, blank=True)
    transcription = models.TextField(blank=True)  # audio/voice transcription via Gemini
    date = models.DateTimeField()
    raw = models.JSONField(default=dict)
    processed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("chat_id", "message_id")
        ordering = ["-date"]

    def __str__(self):
        label = self.text[:60] if self.text else f"[{self.media_type}]"
        return f"[{self.chat_name}] {self.sender_name}: {label}"
