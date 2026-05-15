"""Log a single LLM/AI call to the Usage table. Never raises."""
import logging

from workflows.pricing import estimate_cost_usd

logger = logging.getLogger(__name__)


def log_usage(*, provider: str, model: str, operation: str, source: str,
              prompt_tokens: int = 0, output_tokens: int = 0,
              total_tokens: int | None = None, duration_ms: int = 0,
              ref_type: str = "", ref_id: str | int = "",
              cost_usd: float | None = None, error: str = "") -> None:
    """Persist a usage row. Swallows all exceptions so that telemetry
    never breaks the calling workflow.
    """
    try:
        from usage.models import Usage
        if total_tokens is None:
            total_tokens = prompt_tokens + output_tokens
        if cost_usd is None:
            cost_usd = estimate_cost_usd(provider, model, prompt_tokens, output_tokens)
        Usage.objects.create(
            provider=provider,
            model=model,
            operation=operation,
            source=source,
            prompt_tokens=prompt_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cost_usd=cost_usd,
            duration_ms=duration_ms,
            ref_type=ref_type,
            ref_id=str(ref_id) if ref_id else "",
            error=error[:500] if error else "",
        )
    except Exception:
        logger.exception("Usage logging failed (swallowed)")
