from django.contrib import admin

from .models import RssArticle, RssFeed


@admin.register(RssFeed)
class RssFeedAdmin(admin.ModelAdmin):
    list_display = ["name", "url", "active", "last_fetched"]
    list_editable = ["active"]


@admin.register(RssArticle)
class RssArticleAdmin(admin.ModelAdmin):
    list_display = ["title", "feed", "published_at", "read"]
    list_filter = ["feed", "read"]
    search_fields = ["title"]
    readonly_fields = ["guid", "url", "feed", "published_at", "created_at"]
