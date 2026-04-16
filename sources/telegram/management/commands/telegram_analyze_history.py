"""
Analyze historical Telegram messages with Gemini and write to Google Workspace.

Groups messages by chat + day, processes in chronological order starting from
START_DATE (default 2026-01-01). Marks each message as processed=True after
successful batch analysis.

Safe to re-run: already-processed messages are skipped.
"""
import logging
from collections import defaultdict
from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from sources.telegram.models import TelegramMessage
from workflows.gemini import transcribe_audio, AUDIO_MEDIA_TYPES
from workflows.workflow_telegram import process_batch

logger = logging.getLogger(__name__)

START_DATE = date(2000, 1, 1)  # effectively no lower bound


BATCH_MAX = 25  # target max messages per Gemini call


def _iter_day_chat_batches(start: date, end: date):
    """
    Yield (chat_id, chat_name, date_label, messages) for each batch.

    Days are accumulated until the next day would push the batch over BATCH_MAX.
    A single day with more than BATCH_MAX messages is yielded as-is (no split).

    Uses 2 queries per chat (chat list + all messages) instead of 1 per day,
    so SODA Party (1400+ days) doesn't issue 1400 queries before starting.
    """
    from django.db.models import Min

    chats = (
        TelegramMessage.objects
        .filter(processed=False, date__date__gte=start, date__date__lte=end)
        .values("chat_id", "chat_name")
        .annotate(first_msg=Min("date"))
        .order_by("first_msg")
    )

    seen_chats = set()
    for row in chats:
        chat_id = row["chat_id"]
        if chat_id in seen_chats:
            continue
        seen_chats.add(chat_id)
        chat_name = row["chat_name"]

        # Fetch all unprocessed messages for this chat in one query, group by day in Python
        all_msgs = list(
            TelegramMessage.objects
            .filter(chat_id=chat_id, processed=False, date__date__gte=start, date__date__lte=end)
            .order_by("date")
        )

        # Group by local date
        by_day: dict[date, list] = defaultdict(list)
        for m in all_msgs:
            by_day[m.date.date()].append(m)

        bucket: list = []
        bucket_start: date | None = None
        bucket_end: date | None = None

        for day in sorted(by_day):
            day_msgs = by_day[day]

            if not bucket:
                bucket = day_msgs
                bucket_start = bucket_end = day
            elif len(bucket) + len(day_msgs) <= BATCH_MAX:
                bucket += day_msgs
                bucket_end = day
            else:
                label = bucket_start.isoformat() if bucket_start == bucket_end else f"{bucket_start} → {bucket_end}"
                yield chat_id, chat_name, label, bucket
                bucket = day_msgs
                bucket_start = bucket_end = day

        if bucket:
            label = bucket_start.isoformat() if bucket_start == bucket_end else f"{bucket_start} → {bucket_end}"
            yield chat_id, chat_name, label, bucket


class Command(BaseCommand):
    help = "Analyze Telegram history with Gemini and write to Google Workspace."

    def add_arguments(self, parser):
        parser.add_argument(
            "--start",
            type=str,
            default=START_DATE.isoformat(),
            help="Start date YYYY-MM-DD (default: no lower bound)",
        )
        parser.add_argument(
            "--chat",
            type=str,
            default="",
            help="Comma-separated chat IDs to limit analysis to",
        )
        parser.add_argument(
            "--one-chat",
            action="store_true",
            help="Process only the chat with fewest unprocessed messages, then stop",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print batches without calling Gemini or writing to Google",
        )

    def handle(self, *args, **options):
        from django.db.models import Count

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

        # --one-chat: auto-select the chat with fewest unprocessed messages
        if options["one_chat"] and not only_chats:
            row = (
                TelegramMessage.objects
                .filter(processed=False, date__date__gte=start, date__date__lte=end)
                .values("chat_id", "chat_name")
                .annotate(n=Count("id"))
                .order_by("n")
                .first()
            )
            if not row:
                self.stdout.write("No unprocessed messages found.")
                return
            only_chats = {row["chat_id"]}
            self.stdout.write(f"Auto-selected: {row['chat_name']} ({row['chat_id']}) — {row['n']} unprocessed msgs")

        self.stdout.write(f"Analyzing from {start} to {end}" +
                          (f" (chats: {only_chats})" if only_chats else "") +
                          (" [DRY RUN]" if options["dry_run"] else ""))

        total_batches = 0
        total_contacts = 0
        total_events = 0
        total_todos = 0
        total_msgs_processed = 0

        for chat_id, chat_name, date_label, msgs in _iter_day_chat_batches(start, end):
            if only_chats and chat_id not in only_chats:
                continue

            if not msgs:
                continue

            # Use the start date of the batch as the reference date for Gemini
            date_str = msgs[0].date.strftime("%Y-%m-%d")
            self.stdout.write(
                f"  {date_label} | {chat_name} ({chat_id}) — {len(msgs)} msgs",
                ending=""
            )
            self.stdout.flush()

            if options["dry_run"]:
                self.stdout.write(" [skipped]")
                continue

            # Transcribe audio messages that have a downloaded file
            for m in msgs:
                if m.media_type in AUDIO_MEDIA_TYPES and m.media_path and not m.transcription:
                    abs_path = f"/var/www/big-sync/media/{m.media_path}"
                    if not __import__("os").path.exists(abs_path):
                        continue
                    try:
                        t = transcribe_audio(abs_path)
                        if t:
                            TelegramMessage.objects.filter(pk=m.pk).update(transcription=t)
                            m.transcription = t
                            self.stdout.write(f"\n    [transcribed {m.pk}]", ending="")
                    except Exception as e:
                        self.stdout.write(f"\n    [transcription error {m.pk}: {e}]", ending="")

            batch_data = [
                {
                    "time": m.date.strftime("%H:%M"),
                    "sender": m.sender_name or "Sconosciuto",
                    "text": m.transcription if m.media_type in AUDIO_MEDIA_TYPES and m.transcription else m.text,
                    "media_type": m.media_type,
                }
                for m in msgs
            ]

            try:
                counts = process_batch(chat_name, date_str, batch_data)
            except Exception as e:
                self.stdout.write(f" → GEMINI ERROR: {e} [not marked processed]")
                logger.exception("Gemini error on batch %s %s", chat_name, date_str)
                continue

            # Mark processed only on successful Gemini response
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
