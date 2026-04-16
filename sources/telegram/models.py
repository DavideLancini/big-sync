from django.db import models


class TelegramMessage(models.Model):
    message_id = models.BigIntegerField()
    chat_id = models.BigIntegerField()
    chat_name = models.CharField(max_length=255, blank=True)
    sender_id = models.BigIntegerField(null=True, blank=True)
    sender_name = models.CharField(max_length=255, blank=True)
    text = models.TextField(blank=True)
    date = models.DateTimeField()
    raw = models.JSONField(default=dict)
    processed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("chat_id", "message_id")
        ordering = ["-date"]

    def __str__(self):
        return f"[{self.chat_name}] {self.sender_name}: {self.text[:60]}"
