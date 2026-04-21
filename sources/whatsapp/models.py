from django.db import models


class WaMediaType(models.TextChoices):
    TEXT = "text", "Text"
    PHOTO = "photo", "Photo"
    VIDEO = "video", "Video"
    VOICE = "voice", "Voice"
    AUDIO = "audio", "Audio"
    STICKER = "sticker", "Sticker"
    GIF = "gif", "GIF"
    DOCUMENT = "document", "Document"
    LOCATION = "location", "Location"
    CONTACT = "contact", "Contact"
    UNKNOWN = "unknown", "Unknown"


class WhatsAppMessage(models.Model):
    """Mirror of TelegramMessage, with JID (string) identifiers instead of ints."""
    message_id = models.CharField(max_length=128)
    chat_jid = models.CharField(max_length=128, db_index=True)
    chat_name = models.CharField(max_length=255, blank=True)
    sender_jid = models.CharField(max_length=128, blank=True)
    sender_name = models.CharField(max_length=255, blank=True)
    text = models.TextField(blank=True)
    media_type = models.CharField(
        max_length=20, choices=WaMediaType.choices, default=WaMediaType.TEXT
    )
    media_downloaded = models.BooleanField(default=False)
    media_path = models.CharField(max_length=500, blank=True)
    transcription = models.TextField(blank=True)
    date = models.DateTimeField()
    raw = models.JSONField(default=dict)
    processed = models.BooleanField(default=False)
    is_from_me = models.BooleanField(default=False)
    is_group = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("chat_jid", "message_id")
        ordering = ["-date"]

    def __str__(self):
        label = self.text[:60] if self.text else f"[{self.media_type}]"
        return f"[{self.chat_name}] {self.sender_name}: {label}"
