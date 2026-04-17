"""Dashboard view."""
import subprocess
import sys

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from common.models import Contact, WriteLog
from sources.telegram.models import TelegramMessage


def dashboard(request):
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
    return render(request, "common/dashboard.html", ctx)


@require_POST
def run_command(request, action):
    manage = [sys.executable, "manage.py"]
    commands = {
        "import": manage + ["telegram_import_history"],
        "analyze": manage + ["telegram_analyze_history", "--one-chat"],
        "analyze_all": manage + ["telegram_analyze_history"],
    }
    if action not in commands:
        return JsonResponse({"error": "Unknown action"}, status=400)

    try:
        subprocess.Popen(
            commands[action],
            cwd="/var/www/big-sync",
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return JsonResponse({"status": "started", "action": action})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
