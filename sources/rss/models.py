from django.db import models


TOPICS = [
    ("politica-italiana",       "Politica italiana",        1),
    ("politica-internazionale", "Politica internazionale",  2),
    ("economia",                "Economia & finanza",        3),
    ("tecnologia",              "Tecnologia & AI",           4),
    ("scienza-salute",          "Scienza & salute",          5),
    ("sport",                   "Sport",                     6),
    ("cultura",                 "Cultura & spettacolo",      7),
    ("cronaca",                 "Cronaca",                   8),
    ("ambiente",                "Ambiente & clima",          9),
    ("altro",                   "Altro",                    10),
]


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
    analyzed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-published_at", "-created_at"]

    def __str__(self):
        return self.title

    @property
    def body(self):
        return self.content or self.summary


class RssTopic(models.Model):
    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=100, unique=True)
    order = models.IntegerField(default=0)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return self.name


class RssDailySummary(models.Model):
    topic = models.ForeignKey(RssTopic, on_delete=models.CASCADE, related_name="summaries")
    date = models.DateField()
    text = models.TextField(blank=True)
    article_count = models.IntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("topic", "date")]
        ordering = ["-date", "topic__order"]

    def __str__(self):
        return f"{self.date} – {self.topic.name}"
