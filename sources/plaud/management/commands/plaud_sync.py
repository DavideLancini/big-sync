"""Poll Plaud cloud, download new recordings, save to PlaudRecording."""
import logging
from datetime import datetime, timezone as dt_tz
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.management.base import BaseCommand

from sources.plaud.client import PlaudAuthError, download_audio, list_all
from sources.plaud.models import PlaudRecording

logger = logging.getLogger(__name__)

_TARGET_DIR = Path(settings.MEDIA_ROOT) / "plaud"


class Command(BaseCommand):
    help = "Sync new recordings from Plaud cloud"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=200,
                            help="Max recordings to scan (newest first)")

    def handle(self, *args, **opts):
        try:
            recordings = list_all(page_size=50)[: opts["limit"]]
        except PlaudAuthError as e:
            self.stderr.write(f"AUTH: {e}")
            return

        existing_ids = set(
            PlaudRecording.objects.filter(plaud_id__in=[r["id"] for r in recordings])
            .values_list("plaud_id", flat=True)
        )
        new = [r for r in recordings if r["id"] not in existing_ids]

        self.stdout.write(f"Plaud: {len(recordings)} listed, {len(new)} new")

        _TARGET_DIR.mkdir(parents=True, exist_ok=True)

        for r in new:
            rid = r["id"]
            fname = (r.get("filename") or rid).strip() or rid
            # ensure .mp3 / .wav extension preserved
            ext = Path(fname).suffix.lower() or ".mp3"
            local_name = f"{rid}{ext}"
            dest = _TARGET_DIR / local_name

            try:
                bytes_w = download_audio(rid, str(dest))
            except Exception as e:
                self.stderr.write(f"  ✗ download {rid}: {e}")
                logger.exception("plaud download failed for %s", rid)
                continue

            recorded_at = None
            ts = r.get("start_time") or 0
            if ts:
                # Plaud uses seconds (sometimes ms — detect by magnitude)
                seconds = ts / 1000 if ts > 1e11 else ts
                try:
                    recorded_at = datetime.fromtimestamp(seconds, tz=dt_tz.utc)
                except (OSError, ValueError):
                    recorded_at = None

            with open(dest, "rb") as fh:
                rec = PlaudRecording(
                    plaud_id=rid,
                    original_name=fname,
                    size_bytes=bytes_w or r.get("filesize", 0),
                    duration_ms=int(r.get("duration", 0) or 0),
                    serial_number=r.get("serial_number", "") or "",
                    recorded_at=recorded_at,
                )
                rec.file.save(local_name, File(fh), save=True)

            # remove the temp copy now that it's stored under MEDIA_ROOT/plaud/
            try:
                if rec.file.path != str(dest):
                    dest.unlink()
            except OSError:
                pass

            self.stdout.write(f"  ✓ {rid} {fname} ({bytes_w} bytes)")
