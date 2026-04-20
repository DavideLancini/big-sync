"""Dashboard views."""
import os
import subprocess
import sys

from decouple import config as env_config
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt

from common.models import ActiveSession, Contact, WriteLog
from sources.telegram.models import TelegramMessage

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

    from common.google_billing import billing_summary

    total_contacts = Contact.objects.count()
    total_events = WriteLog.objects.filter(type=WriteLog.TYPE_EVENT).count()
    total_tasks = WriteLog.objects.filter(type=WriteLog.TYPE_TASK).count()
    total_contacts_written = WriteLog.objects.filter(type=WriteLog.TYPE_CONTACT).count()

    recent_activity = WriteLog.objects.order_by("-created_at")[:10]

    telegram_total = TelegramMessage.objects.count()
    telegram_pending = TelegramMessage.objects.filter(processed=False).count()
    telegram_analyzed = telegram_total - telegram_pending

    ctx = {
        "total_contacts": total_contacts,
        "total_events": total_events,
        "total_tasks": total_tasks,
        "total_contacts_written": total_contacts_written,
        "recent_activity": recent_activity,
        "telegram_total": telegram_total,
        "telegram_pending": telegram_pending,
        "telegram_analyzed": telegram_analyzed,
        "gemini_status": _gemini_status(),
        "gemini_studio_url": _GEMINI_STUDIO_URL,
        "billing": billing_summary(),
    }
    return render(request, "common/home.html", ctx)


def rss_dashboard(request):
    if not _is_authenticated(request):
        return redirect("login")

    from sources.rss.models import RssArticle, RssFeed

    feeds = RssFeed.objects.filter(active=True).order_by("name")
    feed_filter = request.GET.get("feed")
    articles = RssArticle.objects.select_related("feed").order_by("-published_at", "-created_at")
    if feed_filter:
        articles = articles.filter(feed_id=feed_filter)
    articles = articles[:200]

    ctx = {
        "feeds": feeds,
        "articles": articles,
        "active_feed": feed_filter,
        "tab": request.GET.get("tab", "news"),
    }
    return render(request, "common/rss.html", ctx)


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
        "rss_fetch": manage + ["rss_fetch"],
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
