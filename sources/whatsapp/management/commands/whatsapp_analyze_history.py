"""
Analyze historical WhatsApp messages with Gemini (mirror of telegram_analyze_history).

Groups messages by chat + day, processes chronologically. Marks each message
processed=True after successful batch analysis. Safe to re-run.
"""
import logging
import os
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone as dt_timezone

from django.core.management.base import BaseCommand
from django.db.models import Count, Min
from django.utils import timezone

from sources.whatsapp.models import WhatsAppMessage
from workflows.gemini import AUDIO_MEDIA_TYPES, transcribe_audio
from workflows.workflow_telegram import process_batch

logger = logging.getLogger(__name__)

START_DATE = date(2000, 1, 1)
BATCH_MAX = 25


def _iter_day_chat_batches(start: date, end: date, only_chat_jid: str | None = None):
    start_dt = datetime(start.year, start.month, start.day, tzinfo=dt_timezone.utc)
    end_dt = datetime(end.year, end.month, end.day, tzinfo=dt_timezone.utc) + timedelta(days=1)

    base_qs = WhatsAppMessage.objects.filter(
        processed=False, date__gte=start_dt, date__lt=end_dt,
    )

    if only_chat_jid is not None:
        chat_rows = base_qs.filter(chat_jid=only_chat_jid).values("chat_jid", "chat_name").annotate(first_msg=Min("date"))
    else:
        chat_rows = base_qs.values("chat_jid", "chat_name").annotate(first_msg=Min("date")).order_by("first_msg")

    seen = set()
    for row in chat_rows:
        jid = row["chat_jid"]
        if jid in seen:
            continue
        seen.add(jid)
        chat_name = row["chat_name"]

        all_msgs = list(
            WhatsAppMessage.objects
            .filter(chat_jid=jid, processed=False, date__gte=start_dt, date__lt=end_dt)
            .order_by("date")
        )

        by_day: dict[date, list] = defaultdict(list)
        for m in all_msgs:
            by_day[m.date.date()].append(m)

        bucket: list = []
        bucket_start = bucket_end = None

        for day in sorted(by_day):
            day_msgs = by_day[day]
            chunks = [day_msgs[i:i + BATCH_MAX] for i in range(0, len(day_msgs), BATCH_MAX)]
            for chunk in chunks:
                if not bucket:
                    bucket = chunk
                    bucket_start = bucket_end = day
                elif len(bucket) + len(chunk) <= BATCH_MAX:
                    bucket += chunk
                    bucket_end = day
                else:
                    label = bucket_start.isoformat() if bucket_start == bucket_end else f"{bucket_start} → {bucket_end}"
                    yield jid, chat_name, label, bucket
                    bucket = chunk
                    bucket_start = bucket_end = day

        if bucket:
            label = bucket_start.isoformat() if bucket_start == bucket_end else f"{bucket_start} → {bucket_end}"
            yield jid, chat_name, label, bucket


class Command(BaseCommand):
    help = "Analyze WhatsApp history with Gemini."

    def add_arguments(self, parser):
        parser.add_argument("--start", type=str, default=START_DATE.isoformat())
        parser.add_argument("--chat", type=str, default="",
                            help="Comma-separated chat JIDs to limit analysis.")
        parser.add_argument("--one-chat", action="store_true",
                            help="Process only the chat with fewest unprocessed messages.")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        try:
            start = date.fromisoformat(options["start"])
        except ValueError:
            self.stderr.write(f"Invalid start date: {options['start']}")
            return

        end = timezone.now().date()

        only_chats: set[str] = {v.strip() for v in options["chat"].split(",") if v.strip()}

        start_dt = datetime(start.year, start.month, start.day, tzinfo=dt_timezone.utc)
        end_dt = datetime(end.year, end.month, end.day, tzinfo=dt_timezone.utc) + timedelta(days=1)
        if options["one_chat"] and not only_chats:
            row = (
                WhatsAppMessage.objects
                .filter(processed=False, date__gte=start_dt, date__lt=end_dt)
                .values("chat_jid", "chat_name")
                .annotate(n=Count("id"))
                .order_by("n")
                .first()
            )
            if not row:
                self.stdout.write("No unprocessed messages found.")
                return
            only_chats = {row["chat_jid"]}
            self.stdout.write(f"Auto-selected: {row['chat_name']} ({row['chat_jid']}) — {row['n']} unprocessed msgs")

        self.stdout.write(f"Analyzing from {start} to {end}" +
                          (f" (chats: {only_chats})" if only_chats else "") +
                          (" [DRY RUN]" if options["dry_run"] else ""))

        total_batches = 0
        total_contacts = total_events = total_todos = 0
        total_msgs = 0

        only_chat_jid = next(iter(only_chats)) if len(only_chats) == 1 else None
        for jid, chat_name, date_label, msgs in _iter_day_chat_batches(start, end, only_chat_jid):
            if only_chats and jid not in only_chats:
                continue
            if not msgs:
                continue

            date_str = msgs[0].date.strftime("%Y-%m-%d")
            self.stdout.write(f"  {date_label} | {chat_name} ({jid}) — {len(msgs)} msgs", ending="")
            self.stdout.flush()

            if options["dry_run"]:
                self.stdout.write(" [skipped]")
                continue

            for m in msgs:
                if m.media_type in AUDIO_MEDIA_TYPES and m.media_path and not m.transcription:
                    abs_path = f"/var/www/big-sync/media/{m.media_path}"
                    if not os.path.exists(abs_path):
                        continue
                    try:
                        t = transcribe_audio(abs_path)
                        if t:
                            WhatsAppMessage.objects.filter(pk=m.pk).update(transcription=t)
                            m.transcription = t
                    except Exception as e:
                        logger.exception("transcribe failed for pk=%s: %s", m.pk, e)

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
                self.stdout.write(f" → GEMINI ERROR: {e}")
                continue

            WhatsAppMessage.objects.filter(pk__in=[m.pk for m in msgs]).update(processed=True)

            total_batches += 1
            total_contacts += counts["contacts"]
            total_events += counts["events"]
            total_todos += counts["todos"]
            total_msgs += len(msgs)
            self.stdout.write(
                f" → contacts:{counts['contacts']} events:{counts['events']} todos:{counts['todos']}"
            )

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. {total_batches} batches, {total_msgs} messages processed.\n"
            f"Written: {total_contacts} contacts, {total_events} events, {total_todos} todos."
        ))
