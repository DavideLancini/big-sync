"""AI-tag unanalyzed Gmail messages and apply labels in Gmail."""
from django.core.management.base import BaseCommand

from sources.email_source.gmail_client import apply_labels_to_message, get_or_create_gmail_label, get_service
from sources.email_source.models import PREDEFINED_TAGS, TAG_COLORS, EmailTag, GmailMessage
from workflows.workflow_email import tag_email


def _seed_tags():
    for name in PREDEFINED_TAGS:
        EmailTag.objects.get_or_create(
            name=name,
            defaults={"color": TAG_COLORS.get(name, "#64748b")},
        )


def _claim(message_id: int) -> bool:
    updated = GmailMessage.objects.filter(id=message_id, analyzed=False).update(analyzed=True)
    return bool(updated)


class Command(BaseCommand):
    help = "AI-tag unanalyzed Gmail messages and apply Gmail labels"

    def add_arguments(self, parser):
        parser.add_argument("--no-labels", action="store_true", help="Skip applying Gmail labels")

    def handle(self, *args, **options):
        _seed_tags()
        apply_gmail_labels = not options["no_labels"]
        service = get_service() if apply_gmail_labels else None

        ids = list(
            GmailMessage.objects.filter(analyzed=False)
            .order_by("-date")
            .values_list("id", flat=True)
        )
        total = len(ids)

        if not total:
            self.stdout.write("Nessun messaggio da analizzare.")
            return

        self.stdout.write(f"Analisi di {total} messaggi...")
        ok = skipped = errors = 0

        for msg_id in ids:
            if not _claim(msg_id):
                skipped += 1
                continue

            try:
                msg = GmailMessage.objects.get(id=msg_id)
                tag_names = tag_email(
                    sender=f"{msg.sender} <{msg.sender_email}>",
                    subject=msg.subject,
                    body=msg.body_text,
                )

                label_ids_to_apply = []
                for tag_name in tag_names:
                    tag, _ = EmailTag.objects.get_or_create(
                        name=tag_name,
                        defaults={"color": TAG_COLORS.get(tag_name, "#64748b")},
                    )
                    msg.tags.add(tag)

                    if apply_gmail_labels:
                        if not tag.gmail_label_id:
                            tag.gmail_label_id = get_or_create_gmail_label(service, tag_name)
                            tag.save(update_fields=["gmail_label_id"])
                        label_ids_to_apply.append(tag.gmail_label_id)

                if apply_gmail_labels and label_ids_to_apply:
                    apply_labels_to_message(service, msg.gmail_id, label_ids_to_apply)

                self.stdout.write(f"  [{', '.join(tag_names)}] {msg.subject[:70]}")
                ok += 1
            except Exception as e:
                GmailMessage.objects.filter(id=msg_id).update(analyzed=False)
                self.stdout.write(f"  ERRORE {msg_id}: {e}")
                errors += 1

        msg_out = f"\nDone. {ok} taggati"
        if skipped:
            msg_out += f", {skipped} già presi"
        if errors:
            msg_out += f", {errors} errori"
        self.stdout.write(msg_out + ".")
