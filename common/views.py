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

    telegram_total = TelegramMessage.objects.count()
    telegram_pending = TelegramMessage.objects.filter(processed=False).count()
    whatsapp_total = WhatsAppMessage.objects.count()
    whatsapp_pending = WhatsAppMessage.objects.filter(processed=False).count()
    rss_total = RssArticle.objects.count()
    rss_unread = RssArticle.objects.filter(read=False).count()

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

    tab = request.GET.get("tab", "news")
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

    audio_path = settings.MEDIA_ROOT / "rss_audio" / f"{displayed_date}.wav"

    ctx = {
        "tab": tab,
        "feeds": feeds,
        "articles": articles,
        "active_feed": feed_filter,
        "summaries": summaries,
        "summary_dates": summary_dates,
        "today": today,
        "displayed_date": displayed_date,
        "audio_exists": audio_path.exists(),
    }
    return render(request, "common/rss.html", ctx)


def rss_audio(request, date_str):
    if not _is_authenticated(request):
        return redirect("login")
    audio_path = settings.MEDIA_ROOT / "rss_audio" / f"{date_str}.wav"
    if not audio_path.exists():
        raise Http404
    return FileResponse(open(audio_path, "rb"), content_type="audio/wav")


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
    }

    if action.startswith("rss_audio:"):
        from datetime import date as date_type
        date_str = action.removeprefix("rss_audio:")
        try:
            date_type.fromisoformat(date_str)
        except ValueError:
            return JsonResponse({"error": "Data non valida"}, status=400)
        commands[action] = manage + ["rss_audio_generate", "--date", date_str, "--force"]

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
