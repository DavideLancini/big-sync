from django.contrib import admin

from .models import EmailTag, GmailMessage, GmailSyncState


@admin.register(EmailTag)
class EmailTagAdmin(admin.ModelAdmin):
    list_display = ["name", "color", "gmail_label_id"]


@admin.register(GmailMessage)
class GmailMessageAdmin(admin.ModelAdmin):
    list_display = ["subject", "sender_email", "date", "analyzed"]
    list_filter = ["analyzed", "tags"]
    search_fields = ["subject", "sender", "sender_email"]
    filter_horizontal = ["tags"]
    readonly_fields = ["gmail_id", "thread_id", "date", "created_at"]


@admin.register(GmailSyncState)
class GmailSyncStateAdmin(admin.ModelAdmin):
    list_display = ["history_id", "last_synced"]
