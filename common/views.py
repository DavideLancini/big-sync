"""Dashboard views."""
import os
import subprocess
import sys

from decouple import config as env_config
from django.conf import settings
from django.http import FileResponse, Http404, JsonResponse, StreamingHttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt

from common.models import ActiveSession, Contact, WriteLog
from sources.telegram.models import TelegramMessage
from sources.whatsapp.models import WhatsAppMessage

_SESSION_KEY = "dashboard_auth"


def _is_authenticated(request):
    return (
        request.session.get(_SESSION_KEY) is True
        and request.session.session_key == ActiveSession.get_current_key()
    )


def login_view(request):
    error = None
    if request.method == "POST":
        password = request.POST.get("password", "")
        if password == env_config("DASHBOARD_PASSWORD"):
            request.session.cycle_key()
            request.session[_SESSION_KEY] = True
            ActiveSession.set_key(request.session.session_key)
            return redirect("home")
        error = "Password errata."
    return render(request, "common/login.html", {"error": error})


def logout_view(request):
    request.session.flush()
    return redirect("login")


_GEMINI_STUDIO_URL = "https://aistudio.google.com/app/apikey"

def _gemini_status() -> dict:
    """Try a minimal Gemini call to verify the key is working."""
    try:
        from workflows.gemini import _get_client
        client = _get_client()
        # list models — cheapest possible call, no tokens billed
        models = list(client.models.list())
        return {"ok": True, "models": len(models)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:80]}


def home(request):
    if not _is_authenticated(request):
        return redirect("login")

    return render(request, "common/home.html", {
        "gemini_studio_url": _GEMINI_STUDIO_URL,
    })


def home_stats_json(request):
    if not _is_authenticated(request):
        return JsonResponse({"error": "Unauthorized"}, status=401)

    from common.google_billing import billing_summary
    from sources.rss.models import RssArticle
    from sources.plaud.models import PlaudRecording

    telegram_total = TelegramMessage.objects.count()
    telegram_pending = TelegramMessage.objects.filter(processed=False).count()
    whatsapp_total = WhatsAppMessage.objects.count()
    whatsapp_pending = WhatsAppMessage.objects.filter(processed=False).count()
    rss_total = RssArticle.objects.count()
    rss_unread = RssArticle.objects.filter(read=False).count()
    plaud_total = PlaudRecording.objects.count()
    plaud_pending = PlaudRecording.objects.filter(processed=False).count()

    recent = [
        {
            "type": e.type,
            "title": e.title,
            "created_at": e.created_at.strftime("%d/%m/%Y %H:%M"),
        }
        for e in WriteLog.objects.order_by("-created_at")[:10]
    ]

    return JsonResponse({
        "total_contacts": Contact.objects.count(),
        "total_events": WriteLog.objects.filter(type=WriteLog.TYPE_EVENT).count(),
        "total_tasks": WriteLog.objects.filter(type=WriteLog.TYPE_TASK).count(),
        "total_contacts_written": WriteLog.objects.filter(type=WriteLog.TYPE_CONTACT).count(),
        "telegram_total": telegram_total,
        "telegram_pending": telegram_pending,
        "telegram_analyzed": telegram_total - telegram_pending,
        "whatsapp_total": whatsapp_total,
        "whatsapp_pending": whatsapp_pending,
        "rss_total": rss_total,
        "rss_unread": rss_unread,
        "plaud_total": plaud_total,
        "plaud_pending": plaud_pending,
        "recent_activity": recent,
        "gemini_status": _gemini_status(),
        "billing": billing_summary(),
    })


def email_dashboard(request):
    if not _is_authenticated(request):
        return redirect("login")

    from sources.email_source.models import EmailTag, GmailMessage

    tag_filter = request.GET.get("tag")
    messages = GmailMessage.objects.prefetch_related("tags").order_by("-date")
    if tag_filter:
        messages = messages.filter(tags__name=tag_filter)
    messages = messages[:200]

    from django.db.models import Count
    tags = EmailTag.objects.annotate(
        count=Count("messages")
    ).filter(count__gt=0).order_by("name")

    ctx = {
        "messages": messages,
        "tags": tags,
        "active_tag": tag_filter,
        "total": GmailMessage.objects.count(),
        "unanalyzed": GmailMessage.objects.filter(analyzed=False).count(),
    }
    return render(request, "common/email.html", ctx)


def email_detail(request, gmail_id):
    if not _is_authenticated(request):
        return redirect("login")

    from sources.email_source.models import GmailMessage

    msg = GmailMessage.objects.prefetch_related("tags").get(gmail_id=gmail_id)
    return render(request, "common/email_detail.html", {"msg": msg})


def rss_dashboard(request):
    if not _is_authenticated(request):
        return redirect("login")

    from django.utils import timezone
    from sources.rss.models import RssArticle, RssDailySummary, RssFeed

    tab = request.GET.get("tab", "summary")
    feeds = RssFeed.objects.filter(active=True).order_by("name")
    feed_filter = request.GET.get("feed")

    articles = RssArticle.objects.select_related("feed").order_by("-published_at", "-created_at")
    if feed_filter:
        articles = articles.filter(feed_id=feed_filter)
    articles = articles[:200]

    today = timezone.localdate()
    summaries = (
        RssDailySummary.objects
        .filter(date=today, article_count__gt=0)
        .select_related("topic")
        .order_by("topic__order")
    )

    # Available summary dates for history navigation
    summary_dates = (
        RssDailySummary.objects
        .values_list("date", flat=True)
        .distinct()
        .order_by("-date")[:10]
    )

    summary_date_str = request.GET.get("summary_date")
    if summary_date_str:
        try:
            from datetime import date as date_type
            summary_date = date_type.fromisoformat(summary_date_str)
            summaries = (
                RssDailySummary.objects
                .filter(date=summary_date, article_count__gt=0)
                .select_related("topic")
                .order_by("topic__order")
            )
        except ValueError:
            pass

    displayed_date = today
    if summary_date_str:
        try:
            from datetime import date as date_type
            displayed_date = date_type.fromisoformat(summary_date_str)
        except ValueError:
            pass

    from sources.rss.models import RssDailyAudio
    audios = {
        a.topic_id: a for a in
        RssDailyAudio.objects.filter(date=displayed_date).select_related("topic")
    }

    audio_sections = []
    missing_or_stale = 0
    for s in summaries:
        audio = audios.get(s.topic_id)
        stale = audio is not None and audio.summary_updated_at < s.updated_at
        if audio is None or stale:
            missing_or_stale += 1
        audio_sections.append({
            "topic": s.topic,
            "summary": s,
            "audio": audio,
            "stale": stale,
        })

    ctx = {
        "tab": tab,
        "feeds": feeds,
        "articles": articles,
        "active_feed": feed_filter,
        "summaries": summaries,
        "summary_dates": summary_dates,
        "today": today,
        "displayed_date": displayed_date,
        "audio_sections": audio_sections,
        "audio_has_any": any(s["audio"] and not s["stale"] for s in audio_sections),
        "audio_missing_or_stale": missing_or_stale,
    }
    return render(request, "common/rss.html", ctx)


def rss_audio(request, date_str, topic_slug):
    if not _is_authenticated(request):
        return redirect("login")
    from sources.rss.models import RssDailyAudio
    try:
        audio = RssDailyAudio.objects.select_related("topic").get(
            date=date_str, topic__slug=topic_slug
        )
    except RssDailyAudio.DoesNotExist:
        raise Http404
    if not audio.file:
        raise Http404
    return FileResponse(audio.file.open("rb"), content_type="audio/wav")


def _pid_alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False


@csrf_exempt
def rss_audio_start(request, date_str):
    """POST: kick off async rss_audio_generate for `date_str` if not already running.

    Returns {job_id, status, total, completed, current_topic_slug, already_running}.
    The subprocess is detached so it survives request/connection end.
    """
    if not _is_authenticated(request):
        return JsonResponse({"error": "auth"}, status=401)
    if request.method != "POST":
        return JsonResponse({"error": "method"}, status=405)

    from datetime import date as _date
    from sources.rss.models import RssAudioJob
    try:
        target = _date.fromisoformat(date_str)
    except ValueError:
        return JsonResponse({"error": "bad date"}, status=400)

    # Already-running job → return it; reap stale rows whose pid is dead.
    running = RssAudioJob.objects.filter(date=target, status=RssAudioJob.STATUS_RUNNING).first()
    if running:
        if running.pid and not _pid_alive(running.pid):
            running.status = RssAudioJob.STATUS_ERROR
            running.error = "process disappeared"
            from django.utils import timezone as _tz
            running.finished_at = _tz.now()
            running.save(update_fields=["status", "error", "finished_at", "updated_at"])
        else:
            return JsonResponse({
                "job_id": running.pk,
                "status": running.status,
                "total": running.total_sections,
                "completed": running.completed_sections,
                "current_topic_slug": running.current_topic_slug,
                "already_running": True,
            })

    job = RssAudioJob.objects.create(date=target, status=RssAudioJob.STATUS_RUNNING)

    manage = [sys.executable, "/var/www/big-sync/manage.py"]
    cmd = manage + ["rss_audio_generate", "--date", date_str, "--job-id", str(job.pk)]

    proc = subprocess.Popen(
        cmd,
        cwd="/var/www/big-sync",
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    job.pid = proc.pid
    job.save(update_fields=["pid", "updated_at"])

    return JsonResponse({
        "job_id": job.pk,
        "status": job.status,
        "total": 0,
        "completed": 0,
        "current_topic_slug": "",
        "already_running": False,
    })


def rss_audio_status(request, date_str):
    """GET: current job state for `date_str` + per-section audio info."""
    if not _is_authenticated(request):
        return JsonResponse({"error": "auth"}, status=401)

    from datetime import date as _date
    from sources.rss.models import RssAudioJob, RssDailyAudio, RssDailySummary
    try:
        target = _date.fromisoformat(date_str)
    except ValueError:
        return JsonResponse({"error": "bad date"}, status=400)

    job = RssAudioJob.objects.filter(date=target).order_by("-started_at").first()
    job_payload = None
    if job:
        # Reap stale running jobs whose process is gone.
        if job.status == RssAudioJob.STATUS_RUNNING and job.pid and not _pid_alive(job.pid):
            job.status = RssAudioJob.STATUS_ERROR
            job.error = "process disappeared"
            from django.utils import timezone as _tz
            job.finished_at = _tz.now()
            job.save(update_fields=["status", "error", "finished_at", "updated_at"])
        job_payload = {
            "id": job.pk,
            "status": job.status,
            "total": job.total_sections,
            "completed": job.completed_sections,
            "current_topic_slug": job.current_topic_slug,
            "error": job.error,
            "started_at": job.started_at.isoformat(),
        }

    audios = {
        a.topic_id: a for a in
        RssDailyAudio.objects.filter(date=target).select_related("topic")
    }
    sections = []
    for s in (RssDailySummary.objects.filter(date=target, article_count__gt=0)
                .select_related("topic").order_by("topic__order")):
        a = audios.get(s.topic_id)
        stale = a is not None and a.summary_updated_at < s.updated_at
        sections.append({
            "topic_slug": s.topic.slug,
            "topic_name": s.topic.name,
            "has_audio": bool(a) and not stale,
            "stale": stale,
            "audio_url": f"/rss/audio/{date_str}/{s.topic.slug}/" if a and not stale else None,
        })
    missing_or_stale = sum(1 for s in sections if not s["has_audio"])

    return JsonResponse({
        "job": job_payload,
        "sections": sections,
        "missing_or_stale": missing_or_stale,
        "running": bool(job_payload and job_payload["status"] == "running"),
    })


def rss_article(request, pk):
    if not _is_authenticated(request):
        return redirect("login")

    from sources.rss.models import RssArticle

    article = RssArticle.objects.select_related("feed").get(pk=pk)
    if not article.read:
        article.read = True
        article.save(update_fields=["read"])
    return render(request, "common/rss_article.html", {"article": article})


_SOURCE_LABELS = {
    "whatsapp":       "WhatsApp",
    "email":          "Email",
    "teams":          "Microsoft Teams",
    "clickup":        "ClickUp",
    "sms":            "SMS",
    "github":         "GitHub",
    "gdrive":         "Google Drive",
    "homeassistant":  "Home Assistant",
    "rss":            "RSS Feed",
}


def source_placeholder(request, source):
    if not _is_authenticated(request):
        return redirect("login")
    label = _SOURCE_LABELS.get(source, source)
    return render(request, "common/placeholder.html", {"source": source, "label": label})


def contacts_dashboard(request):
    if not _is_authenticated(request):
        return redirect("login")

    import re as _re
    from collections import defaultdict

    qs = Contact.objects.filter(merged_into__isnull=True).exclude(name="")
    contacts = list(qs.order_by("name"))

    def _norm(s):
        s = (s or "").lower().strip()
        return _re.sub(r"\s+", " ", s)

    def _ed(a, b, cap=2):
        if a == b:
            return 0
        if abs(len(a) - len(b)) > cap:
            return cap + 1
        prev = list(range(len(b) + 1))
        for i, ca in enumerate(a, 1):
            curr = [i] + [0] * len(b)
            for j, cb in enumerate(b, 1):
                cost = 0 if ca == cb else 1
                curr[j] = min(curr[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost)
            prev = curr
        return prev[len(b)]

    max_dist = max(0, min(4, int(request.GET.get("max_dist", "2"))))
    min_len = max(2, int(request.GET.get("min_len", "4")))

    # Exact-name groups first.
    by_norm = defaultdict(list)
    for c in contacts:
        by_norm[_norm(c.name)].append(c)
    exact_groups = [g for g in by_norm.values() if len(g) > 1]
    in_exact = {c.pk for g in exact_groups for c in g}

    # Fuzzy union-find on remaining contacts, bucketed by first letter.
    remaining = [c for c in contacts if c.pk not in in_exact]
    bucket = defaultdict(list)
    for c in remaining:
        n = _norm(c.name)
        if len(n) >= min_len:
            bucket[n[0]].append(c)
    parent = {c.pk: c.pk for c in remaining}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    if max_dist > 0:
        for items in bucket.values():
            for i in range(len(items)):
                for j in range(i + 1, len(items)):
                    a, b = items[i], items[j]
                    na, nb = _norm(a.name), _norm(b.name)
                    if min(len(na), len(nb)) < min_len:
                        continue
                    if _ed(na, nb, max_dist) <= max_dist:
                        union(a.pk, b.pk)

    fuzzy_groups_map = defaultdict(list)
    for c in remaining:
        fuzzy_groups_map[find(c.pk)].append(c)
    fuzzy_groups = [g for g in fuzzy_groups_map.values() if len(g) > 1]

    def _enrich(group):
        # Sort: canonical-candidate first (most info), then by name
        def score(c):
            return -(
                (3 if c.company else 0)
                + (2 if c.emails else 0)
                + (2 if c.phones else 0)
                + (1 if c.notes_url else 0)
                + (1 if c.aliases else 0)
            )
        group = sorted(group, key=lambda c: (score(c), c.name))
        return [
            {
                "id": c.pk,
                "name": c.name,
                "phones": c.phones or [],
                "emails": c.emails or [],
                "company": c.company,
                "role": c.role,
                "aliases": c.aliases or [],
                "drive": bool(c.notes_url),
            }
            for c in group
        ]

    ctx = {
        "exact_groups": [_enrich(g) for g in exact_groups],
        "fuzzy_groups": [_enrich(g) for g in fuzzy_groups],
        "total_active": len(contacts),
        "max_dist": max_dist,
        "min_len": min_len,
    }
    return render(request, "common/contacts.html", ctx)


@csrf_exempt
def contacts_merge_action(request):
    """POST JSON {canonical_id, merge_ids: [...], delete_google: bool}."""
    if not _is_authenticated(request):
        return JsonResponse({"error": "auth"}, status=401)
    if request.method != "POST":
        return JsonResponse({"error": "method"}, status=405)

    import json
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "invalid json"}, status=400)

    canonical_id = payload.get("canonical_id")
    merge_ids = payload.get("merge_ids") or []
    delete_google = bool(payload.get("delete_google", True))
    if not canonical_id or not merge_ids:
        return JsonResponse({"error": "missing canonical_id or merge_ids"}, status=400)

    from outputs.contacts import merge_contacts
    try:
        result = merge_contacts(int(canonical_id),
                                  [int(i) for i in merge_ids],
                                  delete_google=delete_google)
    except Contact.DoesNotExist:
        return JsonResponse({"error": "contact not found"}, status=404)
    except Exception as e:
        return JsonResponse({"error": str(e)[:300]}, status=500)
    return JsonResponse(result)


def items_dashboard(request):
    if not _is_authenticated(request):
        return redirect("login")

    from datetime import timedelta
    from django.utils import timezone
    from common.models import CachedEvent

    now = timezone.now()
    past = request.GET.get("past", "7")
    fut = request.GET.get("future", "30")
    try:
        past_days = max(0, int(past))
    except ValueError:
        past_days = 7
    try:
        future_days = max(1, int(fut))
    except ValueError:
        future_days = 30

    qs = CachedEvent.objects.filter(
        deleted_at__isnull=True,
        start_at__gte=now - timedelta(days=past_days),
        start_at__lte=now + timedelta(days=future_days),
    ).order_by("start_at")

    events = list(qs.filter(is_todo=False)[:300])
    todos = list(qs.filter(is_todo=True)[:300])

    ctx = {
        "events": events,
        "todos": todos,
        "past_days": past_days,
        "future_days": future_days,
    }
    return render(request, "common/items.html", ctx)


@csrf_exempt
def item_action(request, google_id):
    """JSON endpoint: POST {action: "delete"|"update", calendar_id, ...}."""
    if not _is_authenticated(request):
        return JsonResponse({"error": "auth"}, status=401)
    if request.method != "POST":
        return JsonResponse({"error": "method"}, status=405)

    import json
    from common.models import CachedEvent
    from outputs.calendar import delete_event, update_event

    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "invalid json"}, status=400)

    action = payload.get("action")
    calendar_id = payload.get("calendar_id") or "primary"

    cached = CachedEvent.objects.filter(
        google_id=google_id, calendar_id=calendar_id, deleted_at__isnull=True,
    ).first()
    if not cached:
        return JsonResponse({"error": "not found"}, status=404)

    if action == "delete":
        ok = delete_event(google_id, calendar_id)
        return JsonResponse({"ok": ok})

    if action == "update":
        fields = {}
        if "title" in payload:
            fields["summary"] = payload["title"]
        if "location" in payload:
            fields["location"] = payload["location"]
        if "description" in payload:
            fields["description"] = payload["description"]
        if not fields:
            return JsonResponse({"error": "no fields"}, status=400)
        ok = update_event(google_id, calendar_id, fields)
        return JsonResponse({"ok": ok})

    return JsonResponse({"error": "unknown action"}, status=400)


_WHATSAPP_REPAIR_DATE = None  # set below
def _init_repair_date():
    from datetime import date
    global _WHATSAPP_REPAIR_DATE
    _WHATSAPP_REPAIR_DATE = date(2026, 4, 24)
_init_repair_date()


def whatsapp_dashboard(request):
    if not _is_authenticated(request):
        return redirect("login")

    from datetime import date
    today = date.today()
    days_to_repair = (_WHATSAPP_REPAIR_DATE - today).days

    total_msgs = WhatsAppMessage.objects.count()
    analyzed_msgs = WhatsAppMessage.objects.filter(processed=True).count()

    last_received = WhatsAppMessage.objects.order_by("-date").first()
    last_analyzed = WhatsAppMessage.objects.filter(processed=True).order_by("-date").first()

    last_contact = WriteLog.objects.filter(type=WriteLog.TYPE_CONTACT).first()
    last_event = WriteLog.objects.filter(type=WriteLog.TYPE_EVENT).first()
    last_task = WriteLog.objects.filter(type=WriteLog.TYPE_TASK).first()

    total_contacts = Contact.objects.count()
    contacts_with_notes = Contact.objects.exclude(notes_url="").count()

    ctx = {
        "total_msgs": total_msgs,
        "analyzed_msgs": analyzed_msgs,
        "pending_msgs": total_msgs - analyzed_msgs,
        "analyzed_pct": round(analyzed_msgs / total_msgs * 100) if total_msgs else 0,
        "last_received": last_received,
        "last_analyzed": last_analyzed,
        "last_contact": last_contact,
        "last_event": last_event,
        "last_task": last_task,
        "total_contacts": total_contacts,
        "contacts_with_notes": contacts_with_notes,
        "repair_date": _WHATSAPP_REPAIR_DATE,
        "days_to_repair": days_to_repair,
    }
    return render(request, "common/whatsapp.html", ctx)


def plaud_dashboard(request):
    if not _is_authenticated(request):
        return redirect("login")

    from sources.plaud.models import PlaudRecording

    total = PlaudRecording.objects.count()
    processed = PlaudRecording.objects.filter(processed=True).count()
    recordings = PlaudRecording.objects.order_by("-created_at")[:50]
    return render(request, "common/plaud.html", {
        "total": total,
        "processed": processed,
        "pending": total - processed,
        "recordings": recordings,
    })


def plaud_detail(request, pk):
    """JSON: titolo, trascrizione, riassunto, URL audio per il modale."""
    if not _is_authenticated(request):
        return JsonResponse({"error": "auth"}, status=401)
    from sources.plaud.models import PlaudRecording
    try:
        r = PlaudRecording.objects.get(pk=pk)
    except PlaudRecording.DoesNotExist:
        return JsonResponse({"error": "not found"}, status=404)
    return JsonResponse({
        "id": r.pk,
        "title": r.title or "",
        "original_name": r.original_name or "",
        "transcription": r.transcription or "",
        "summary": r.summary or "",
        "audio_url": f"/plaud/{r.pk}/audio/",
        "created_at": r.created_at.isoformat() if r.created_at else "",
        "processed": r.processed,
        "summarized": r.summarized,
        "error": r.error or "",
    })


def plaud_audio(request, pk):
    """Stream the audio file. ?download=1 → Content-Disposition attachment."""
    if not _is_authenticated(request):
        return redirect("login")
    from sources.plaud.models import PlaudRecording
    try:
        r = PlaudRecording.objects.get(pk=pk)
    except PlaudRecording.DoesNotExist:
        raise Http404
    if not r.file:
        raise Http404
    import mimetypes, os as _os
    mime = mimetypes.guess_type(r.file.name)[0] or "audio/mpeg"
    fname = _os.path.basename(r.original_name or r.file.name)
    resp = FileResponse(r.file.open("rb"), content_type=mime)
    if request.GET.get("download"):
        resp["Content-Disposition"] = f'attachment; filename="{fname}"'
    return resp


@csrf_exempt
def plaud_upload(request):
    if not _is_authenticated(request):
        return JsonResponse({"error": "Unauthorized"}, status=401)
    if request.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)

    from sources.plaud.models import PlaudRecording

    files = request.FILES.getlist("file")
    if not files:
        return JsonResponse({"error": "No file"}, status=400)

    created = []
    for f in files:
        rec = PlaudRecording.objects.create(
            file=f,
            original_name=f.name,
            size_bytes=f.size,
        )
        created.append({"id": rec.pk, "name": rec.original_name})
    return JsonResponse({"created": created})


def telegram_dashboard(request):
    if not _is_authenticated(request):
        return redirect("login")

    total_msgs = TelegramMessage.objects.count()
    analyzed_msgs = TelegramMessage.objects.filter(processed=True).count()

    last_received = TelegramMessage.objects.order_by("-date").first()
    last_analyzed = TelegramMessage.objects.filter(processed=True).order_by("-date").first()

    last_contact = WriteLog.objects.filter(type=WriteLog.TYPE_CONTACT).first()
    last_event = WriteLog.objects.filter(type=WriteLog.TYPE_EVENT).first()
    last_task = WriteLog.objects.filter(type=WriteLog.TYPE_TASK).first()

    total_contacts = Contact.objects.count()
    contacts_with_notes = Contact.objects.exclude(notes_url="").count()

    ctx = {
        "total_msgs": total_msgs,
        "analyzed_msgs": analyzed_msgs,
        "pending_msgs": total_msgs - analyzed_msgs,
        "analyzed_pct": round(analyzed_msgs / total_msgs * 100) if total_msgs else 0,
        "last_received": last_received,
        "last_analyzed": last_analyzed,
        "last_contact": last_contact,
        "last_event": last_event,
        "last_task": last_task,
        "total_contacts": total_contacts,
        "contacts_with_notes": contacts_with_notes,
    }
    return render(request, "common/telegram.html", ctx)


@csrf_exempt
def run_command(request, action):
    if not _is_authenticated(request):
        return JsonResponse({"error": "Unauthorized"}, status=401)

    manage = [sys.executable, "-u", "manage.py"]
    commands = {
        "import": manage + ["telegram_import_history", "--gap-check"],
        "analyze": manage + ["telegram_analyze_history", "--one-chat"],
        "analyze_all": manage + ["telegram_analyze_history"],
        "rss_update": manage + ["rss_update"],
        "gmail_import": manage + ["gmail_import", "--full"],
        "gmail_sync": manage + ["gmail_sync"],
        "gmail_analyze": manage + ["gmail_analyze"],
        "wa_analyze": manage + ["whatsapp_analyze_history", "--one-chat"],
        "wa_analyze_all": manage + ["whatsapp_analyze_history"],
        "plaud_sync": manage + ["plaud_sync"],
        "plaud_process": manage + ["plaud_process_pending"],
        "plaud_summarize": manage + ["plaud_summarize_pending"],
    }

    if action not in commands:
        return JsonResponse({"error": "Unknown action"}, status=400)

    def event_stream():
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        try:
            proc = subprocess.Popen(
                commands[action],
                cwd="/var/www/big-sync",
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
            )
            for line in proc.stdout:
                yield f"data: {line.rstrip()}\n\n"
            proc.wait()
            yield f"data: [exitcode={proc.returncode}]\n\n"
            yield "event: done\ndata: done\n\n"
        except Exception as e:
            yield f"data: ERROR: {e}\n\n"
            yield "event: done\ndata: done\n\n"

    response = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
    response["X-Accel-Buffering"] = "no"
    response["Cache-Control"] = "no-cache"
    return response
