from django.db import models


class Usage(models.Model):
    """Single AI/LLM API call. Provider-agnostic so we can track
    Gemini today and other vendors (OpenAI, Anthropic, ElevenLabs...)
    in the same table tomorrow.
    """
    provider = models.CharField(max_length=32, db_index=True)
    model = models.CharField(max_length=64, db_index=True)
    operation = models.CharField(max_length=32, db_index=True)
    source = models.CharField(max_length=32, db_index=True)

    prompt_tokens = models.IntegerField(default=0)
    output_tokens = models.IntegerField(default=0)
    total_tokens = models.IntegerField(default=0)

    cost_usd = models.DecimalField(max_digits=10, decimal_places=6, default=0)
    duration_ms = models.IntegerField(default=0)

    ref_type = models.CharField(max_length=64, blank=True, default="")
    ref_id = models.CharField(max_length=64, blank=True, default="")

    error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["-created_at", "source"]),
            models.Index(fields=["-created_at", "provider", "model"]),
        ]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.provider}/{self.model} {self.operation} {self.cost_usd}"
