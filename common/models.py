from django.db import models


class Contact(models.Model):
    """Local cache of Google Contacts for fast deduplication."""
    resource_name = models.CharField(max_length=100, unique=True, blank=True, db_index=True)
    name = models.CharField(max_length=255, blank=True, db_index=True)
    phones = models.JSONField(default=list)   # list of normalized digits-only strings
    emails = models.JSONField(default=list)   # list of lowercase email strings
    company = models.CharField(max_length=255, blank=True)
    role = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)
    notes_url = models.URLField(max_length=500, blank=True)  # Google Drive file URL when notes overflow
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name or self.resource_name


class WriteLog(models.Model):
    """Tracks every contact/event/task written to Google Workspace."""
    TYPE_CONTACT = "contact"
    TYPE_EVENT = "event"
    TYPE_TASK = "task"
    TYPE_CHOICES = [
        (TYPE_CONTACT, "Contact"),
        (TYPE_EVENT, "Event"),
        (TYPE_TASK, "Task"),
    ]
    type = models.CharField(max_length=20, choices=TYPE_CHOICES, db_index=True)
    title = models.CharField(max_length=255)
    detail = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.type}: {self.title}"


class ContactsSyncLog(models.Model):
    """Tracks when the local contacts cache was last synced from Google."""
    synced_at = models.DateTimeField(auto_now_add=True)
    contacts_count = models.IntegerField(default=0)

    class Meta:
        ordering = ["-synced_at"]
        get_latest_by = "synced_at"

    def __str__(self):
        return f"{self.synced_at:%Y-%m-%d %H:%M} — {self.contacts_count} contacts"
