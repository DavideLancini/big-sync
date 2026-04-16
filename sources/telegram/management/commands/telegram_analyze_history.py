"""
Analyze historical Telegram messages with Gemini and write to Google Workspace.

Groups messages by chat + day, processes in chronological order starting from
START_DATE (default 2026-01-01). Marks each message as processed=True after
successful batch analysis.

Safe to re-run: already-processed messages are skipped.
"""
import logging
from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from sources.telegram.models import TelegramMessage
from workflows.workflow_telegram import process_batch

logger = logging.getLogger(__name__)

START_DATE = date(2026, 1, 1)


def _iter_day_chat_batches(start: date, end: date):
    """
    Yield (chat_id, chat_name, day, messages_qs) for each chat+day combo
    that has unprocessed messages, in chronological order.
    """
    from django.db.models.functions import TruncDate
    from django.db.models import Min

    qs = (
        TelegramMessage.objects
        .filter(processed=False, date__date__gte=start, date__date__lte=end)
        .values("chat_id", "chat_name")
        .annotate(first_msg=Min("date"))
        .order_by("first_msg")
    )

    chat_days_seen = set()
    for row in qs:
        chat_id = row["chat_id"]
        chat_name = row["chat_name"]

        # Get all days for this chat that have unprocessed messages
        days_qs = (
            TelegramMessage.objects
            .filter(chat_id=chat_id, processed=False, date__date__gte=start, date__date__lte=end)
            .dates("date", "day", order="ASC")
        )
        for day in days_qs:
            key = (chat_id, day)
            if key in chat_days_seen:
                continue
            chat_days_seen.add(key)

            msgs_qs = (
                TelegramMessage.objects
                .filter(chat_id=chat_id, processed=False, date__date=day)
                .order_by("date")
            )
            yield chat_id, chat_name, day, msgs_qs


class Command(BaseCommand):
    help = "Analyze Telegram history with Gemini and write to Google Workspace."

    def add_arguments(self, parser):
        parser.add_argument(
            "--start",
            type=str,
            default=START_DATE.isoformat(),
            help="Start date YYYY-MM-DD (default: 2026-01-01)",
        )
        parser.add_argument(
            "--chat",
            type=str,
            default="",
            help="Comma-separated chat IDs to limit analysis to",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print batches without calling Gemini or writing to Google",
        )

    def handle(self, *args, **options):
        try:
            start = date.fromisoformat(options["start"])
        except ValueError:
            self.stderr.write(f"Invalid start date: {options['start']}")
            return

        end = timezone.now().date()

        only_chats: set[int] = set()
        for val in options["chat"].split(","):
            val = val.strip()
            if val:
                try:
                    only_chats.add(int(val))
                except ValueError:
                    pass

        self.stdout.write(f"Analyzing from {start} to {end}" +
                          (f" (chats: {only_chats})" if only_chats else "") +
                          (" [DRY RUN]" if options["dry_run"] else ""))

        total_batches = 0
        total_contacts = 0
        total_events = 0
        total_todos = 0
        total_msgs_processed = 0

        for chat_id, chat_name, day, msgs_qs in _iter_day_chat_batches(start, end):
            if only_chats and chat_id not in only_chats:
                continue

            msgs = list(msgs_qs)
            if not msgs:
                continue

            date_str = day.isoformat()
            self.stdout.write(
                f"  {date_str} | {chat_name} ({chat_id}) — {len(msgs)} msgs",
                ending=""
            )
            self.stdout.flush()

            if options["dry_run"]:
                self.stdout.write(" [skipped]")
                continue

            batch_data = [
                {
                    "time": m.date.strftime("%H:%M"),
                    "sender": m.sender_name or "Sconosciuto",
                    "text": m.text,
                    "media_type": m.media_type,
                }
                for m in msgs
            ]

            counts = process_batch(chat_name, date_str, batch_data)

            # Mark all messages in this batch as processed
            msg_ids = [m.pk for m in msgs]
            TelegramMessage.objects.filter(pk__in=msg_ids).update(processed=True)

            total_batches += 1
            total_contacts += counts["contacts"]
            total_events += counts["events"]
            total_todos += counts["todos"]
            total_msgs_processed += len(msgs)

            self.stdout.write(
                f" → contacts:{counts['contacts']} events:{counts['events']} todos:{counts['todos']}"
            )

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. {total_batches} batches, {total_msgs_processed} messages processed.\n"
            f"Written: {total_contacts} contacts, {total_events} events, {total_todos} todos."
        ))
