from django.db import models


class RssFeed(models.Model):
    name = models.CharField(max_length=100)
    url = models.URLField(max_length=512, unique=True)
    active = models.BooleanField(default=True)
    last_fetched = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class RssArticle(models.Model):
    feed = models.ForeignKey(RssFeed, on_delete=models.CASCADE, related_name="articles")
    guid = models.CharField(max_length=1024, unique=True)
    title = models.CharField(max_length=1024)
    url = models.URLField(max_length=2048)
    summary = models.TextField(blank=True)
    content = models.TextField(blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-published_at", "-created_at"]

    def __str__(self):
        return self.title

    @property
    def body(self):
        return self.content or self.summary
