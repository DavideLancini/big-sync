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
    aliases = models.JSONField(default=list)  # list of lowercase alternative names ("Ghira", "Ghiraffa", "G.")
    merged_into = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="merged_from",
    )  # if set, this contact has been merged into another and should not be used
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name or self.resource_name

    def resolve(self) -> "Contact":
        """Follow merged_into chain to the canonical contact."""
        c = self
        seen = {c.pk}
        while c.merged_into_id and c.merged_into_id not in seen:
            c = c.merged_into
            seen.add(c.pk)
        return c


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


class CachedEvent(models.Model):
    """Local cache of every event/todo on every Google calendar.

    Used as the primary source for dedup decisions: outputs/calendar.py and
    outputs/todos.py consult this table instead of querying Google on every
    write. Populated and refreshed by the sync_calendar command.

    Soft-delete via deleted_at: when an event disappears from Google we keep
    the row but mark it deleted so historical lookups still work.
    """
    google_id = models.CharField(max_length=512, db_index=True)
    calendar_id = models.CharField(max_length=255, db_index=True)
    calendar_name = models.CharField(max_length=100, blank=True, default="")

    title = models.CharField(max_length=512, blank=True, default="")
    start_at = models.DateTimeField(null=True, blank=True, db_index=True)
    end_at = models.DateTimeField(null=True, blank=True)
    all_day = models.BooleanField(default=False)
    location = models.CharField(max_length=512, blank=True, default="")
    description = models.TextField(blank=True, default="")
    attendees = models.JSONField(default=list)  # list of dicts {email, name, status}
    meet_link = models.URLField(max_length=512, blank=True, default="")
    organizer_email = models.CharField(max_length=255, blank=True, default="")
    is_todo = models.BooleanField(default=False, db_index=True)
    created_by_us = models.BooleanField(default=False)

    raw = models.JSONField(default=dict)  # full Google payload for forensic lookups

    last_seen_at = models.DateTimeField(db_index=True)  # last time we saw this in a Google sync
    synced_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ["-start_at"]
        unique_together = [("google_id", "calendar_id")]
        indexes = [
            models.Index(fields=["start_at", "is_todo"]),
            models.Index(fields=["calendar_id", "-start_at"]),
        ]

    def __str__(self):
        prefix = "[todo] " if self.is_todo else ""
        return f"{prefix}{self.title} @ {self.start_at}"


class ActiveSession(models.Model):
    """Singleton: tracks the one currently valid dashboard session."""
    id = models.IntegerField(primary_key=True, default=1, editable=False)
    session_key = models.CharField(max_length=64, blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def get_current_key(cls) -> str:
        obj, _ = cls.objects.get_or_create(id=1)
        return obj.session_key

    @classmethod
    def set_key(cls, key: str):
        cls.objects.update_or_create(id=1, defaults={"session_key": key})
