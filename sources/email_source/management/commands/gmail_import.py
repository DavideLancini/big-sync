"""Import Gmail messages into the database."""
import time

from django.core.management.base import BaseCommand

from sources.email_source.gmail_client import get_service, parse_message
from sources.email_source.models import GmailMessage, GmailSyncState


def _save_message(msg: dict) -> bool:
    """Parse and save message. Returns True if new."""
    data = parse_message(msg)
    _, created = GmailMessage.objects.get_or_create(
        gmail_id=data["gmail_id"],
        defaults={k: v for k, v in data.items() if k != "gmail_id"},
    )
    return created


def _get_history_id(service) -> str:
    profile = service.users().getProfile(userId="me").execute()
    return str(profile["historyId"])


def full_import(service, stdout, max_results=None) -> tuple[int, str]:
    """Fetch all messages. Returns (saved_count, latest_history_id)."""
    stdout.write("Import completo di tutti i messaggi...")
    saved = skipped = 0
    page_token = None

    while True:
        params = {"userId": "me", "maxResults": 500}
        if page_token:
            params["pageToken"] = page_token
        if max_results:
            params["maxResults"] = min(500, max_results - saved - skipped)

        result = service.users().messages().list(**params).execute()
        msgs = result.get("messages", [])
        if not msgs:
            break

        for ref in msgs:
            if GmailMessage.objects.filter(gmail_id=ref["id"]).exists():
                skipped += 1
                continue
            try:
                msg = service.users().messages().get(
                    userId="me", id=ref["id"], format="full"
                ).execute()
                if _save_message(msg):
                    saved += 1
                    stdout.write(f"  +{saved} {msg.get('snippet', '')[:60]}")
            except Exception as e:
                stdout.write(f"  WARN {ref['id']}: {e}")
            time.sleep(0.05)  # avoid rate limit

        if max_results and (saved + skipped) >= max_results:
            break

        page_token = result.get("nextPageToken")
        if not page_token:
            break

    history_id = _get_history_id(service)
    stdout.write(f"Importati {saved} nuovi, {skipped} già presenti. historyId: {history_id}")
    return saved, history_id


def incremental_import(service, history_id: str, stdout) -> tuple[int, str]:
    """Fetch only messages added since history_id."""
    new_ids: set[str] = set()
    page_token = None

    try:
        while True:
            params = {
                "userId": "me",
                "startHistoryId": history_id,
                "historyTypes": ["messageAdded"],
            }
            if page_token:
                params["pageToken"] = page_token
            result = service.users().history().list(**params).execute()

            for record in result.get("history", []):
                for added in record.get("messagesAdded", []):
                    new_ids.add(added["message"]["id"])

            page_token = result.get("nextPageToken")
            if not page_token:
                break
    except Exception as e:
        stdout.write(f"History API error: {e} — falling back to full import")
        return full_import(service, stdout)

    saved = 0
    for gmail_id in new_ids:
        if GmailMessage.objects.filter(gmail_id=gmail_id).exists():
            continue
        try:
            msg = service.users().messages().get(
                userId="me", id=gmail_id, format="full"
            ).execute()
            if _save_message(msg):
                saved += 1
                stdout.write(f"  +{saved} {msg.get('snippet', '')[:60]}")
        except Exception as e:
            stdout.write(f"  WARN {gmail_id}: {e}")

    new_history_id = _get_history_id(service)
    stdout.write(f"Importati {saved} nuovi. historyId: {new_history_id}")
    return saved, new_history_id


class Command(BaseCommand):
    help = "Import Gmail messages (incremental by default, --full for all)"

    def add_arguments(self, parser):
        parser.add_argument("--full", action="store_true", help="Re-import all messages")
        parser.add_argument("--max", type=int, default=None, help="Max messages for full import")

    def handle(self, *args, **options):
        from django.utils import timezone
        service = get_service()
        state = GmailSyncState.get()

        if options["full"] or not state.history_id:
            saved, history_id = full_import(service, self.stdout, max_results=options["max"])
        else:
            saved, history_id = incremental_import(service, state.history_id, self.stdout)

        state.history_id = history_id
        state.last_synced = timezone.now()
        state.save()
