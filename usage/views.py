from datetime import timedelta

from django.db.models import Count, Sum
from django.shortcuts import redirect, render
from django.utils import timezone

from common.views import _is_authenticated
from usage.models import Usage


def usage_dashboard(request):
    if not _is_authenticated(request):
        return redirect("login")

    cutoff = timezone.now() - timedelta(days=30)
    qs = Usage.objects.filter(created_at__gte=cutoff)

    totals = qs.aggregate(
        calls=Count("id"),
        prompt=Sum("prompt_tokens"),
        output=Sum("output_tokens"),
        total=Sum("total_tokens"),
        cost=Sum("cost_usd"),
    )

    by_source = list(
        qs.values("source")
        .annotate(calls=Count("id"), tokens=Sum("total_tokens"), cost=Sum("cost_usd"))
        .order_by("-cost")
    )
    by_model = list(
        qs.values("model")
        .annotate(calls=Count("id"), tokens=Sum("total_tokens"), cost=Sum("cost_usd"))
        .order_by("-cost")
    )
    by_operation = list(
        qs.values("operation")
        .annotate(calls=Count("id"), tokens=Sum("total_tokens"), cost=Sum("cost_usd"))
        .order_by("-cost")
    )

    by_day = list(
        qs.extra(select={"day": "DATE(created_at)"})
        .values("day")
        .annotate(calls=Count("id"), tokens=Sum("total_tokens"), cost=Sum("cost_usd"))
        .order_by("-day")
    )

    recent = list(qs.order_by("-created_at")[:50])
    errors = list(qs.exclude(error="").order_by("-created_at")[:20])

    ctx = {
        "totals": totals,
        "by_source": by_source,
        "by_model": by_model,
        "by_operation": by_operation,
        "by_day": by_day,
        "recent": recent,
        "errors": errors,
        "since": cutoff,
    }
    return render(request, "usage/dashboard.html", ctx)
