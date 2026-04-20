from django.db import models

TAG_COLORS = {
    "Lavoro":       "#3b82f6",
    "Personale":    "#8b5cf6",
    "Finanze":      "#22c55e",
    "Acquisti":     "#f97316",
    "Newsletter":   "#6366f1",
    "Notifiche":    "#64748b",
    "Viaggi":       "#14b8a6",
    "Salute":       "#ef4444",
    "Urgente":      "#f59e0b",
    "Unsubscribe":  "#fb923c",
    "Spam":         "#374151",
}

PREDEFINED_TAGS = list(TAG_COLORS.keys())


class EmailTag(models.Model):
    name = models.CharField(max_length=50, unique=True)
    color = models.CharField(max_length=20, default="#64748b")
    gmail_label_id = models.CharField(max_length=100, blank=True)

    def __str__(self):
        return self.name


class GmailMessage(models.Model):
    gmail_id = models.CharField(max_length=100, unique=True)
    thread_id = models.CharField(max_length=100, blank=True)
    subject = models.CharField(max_length=1000, blank=True)
    sender = models.CharField(max_length=500, blank=True)
    sender_email = models.CharField(max_length=254, blank=True)
    snippet = models.TextField(blank=True)
    body_text = models.TextField(blank=True)
    date = models.DateTimeField(null=True, blank=True)
    gmail_labels = models.JSONField(default=list)
    tags = models.ManyToManyField(EmailTag, blank=True, related_name="messages")
    analyzed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date"]

    def __str__(self):
        return self.subject or f"<{self.gmail_id}>"


class GmailSyncState(models.Model):
    id = models.IntegerField(primary_key=True, default=1, editable=False)
    history_id = models.CharField(max_length=50, blank=True)
    last_synced = models.DateTimeField(null=True, blank=True)

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(id=1)
        return obj
