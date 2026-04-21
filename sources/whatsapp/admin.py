from django.contrib import admin

from .models import WhatsAppMessage


@admin.register(WhatsAppMessage)
class WhatsAppMessageAdmin(admin.ModelAdmin):
    list_display = ["date", "chat_name", "sender_name", "media_type", "processed"]
    list_filter = ["processed", "media_type", "is_group", "is_from_me"]
    search_fields = ["text", "chat_name", "sender_name", "chat_jid"]
    readonly_fields = ["message_id", "chat_jid", "sender_jid", "date", "created_at", "raw"]
