"""One-off: delete low-information [todo] events + duplicate titles."""
import os
import sys
from datetime import datetime, timedelta

import django

sys.path.insert(0, "/var/www/big-sync")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from googleapiclient.discovery import build
from common.google_auth import get_credentials

BLACKLIST = {
    # personal / bodily / trivial routine
    "mangiare cena", "portare fuori il cane", "prendere caffè (fuori)",
    "take a tactical break", "charge/turn on pc", "take dog out",
    "bere il caffè", "andare in bagno", "fare la spesa", "cenare",
    "fare la doccia", "comprare farina gialla nostrana per polenta",
    "chiamare per la carne", "prendere formaggio a vigolo",
    "preparare mombolini di carne", "pranzo al volo", "bevo un ginseng",
    "reboot my wifi",
    # vague single-word / catch-all
    "lavorare in autonomia", "riprendere il lavoro",
    "sistemare un'attività urgente", "rifiniture", "fix su deploy",
    "animazione", "issues", "ricerca sentenze",
    "fare le cose che mancano", "finire 'sta cosa'", "lavorare fulltime",
    "finire un'attività", "entra", "dedica 20-30 minuti a una task",
    # bare pings without context
    "aggiornare francesco", "rispondere a gray", "contattare gray",
    "rispondi nel gruppo", "chiamare davide", "sentirsi con francesco",
    "sentirsi con gray", "sentirsi con ash", "avvisare sara",
    "scrivere a nigga", "richiamare francesco circosta",
    "check summarized tasks document", "rispondere a ace sul gruppo",
    "fornire aggiornamento su attività notturna",
    # test / joke
    "[test] task con orario 11:46-12:01",
    "ricordarsi che giovanni ammazza i gatti",
}

svc = build("calendar", "v3", credentials=get_credentials())
now = datetime.utcnow()
events, pt = [], None
while True:
    p = {"calendarId": "primary",
         "timeMin": (now - timedelta(days=365)).isoformat() + "Z",
         "timeMax": (now + timedelta(days=365)).isoformat() + "Z",
         "singleEvents": True, "q": "[todo]", "maxResults": 2500}
    if pt: p["pageToken"] = pt
    r = svc.events().list(**p).execute()
    events += r.get("items", [])
    pt = r.get("nextPageToken")
    if not pt: break

todos = [e for e in events if e.get("summary", "").startswith("[todo] ")]
# Sort by start time so "first occurrence" is chronological
def start_key(e):
    s = e["start"].get("dateTime") or e["start"].get("date") or ""
    return s
todos.sort(key=start_key)

seen_titles: set[str] = set()
to_delete: list[tuple[str, str, str]] = []  # (id, title, reason)

for e in todos:
    title = e["summary"][7:].strip()
    norm = title.lower().strip()

    if norm in BLACKLIST:
        to_delete.append((e["id"], title, "blacklist"))
        continue

    if norm in seen_titles:
        to_delete.append((e["id"], title, "duplicate"))
        continue

    seen_titles.add(norm)

print(f"Found {len(todos)} [todo] events. Marked {len(to_delete)} for deletion.")
blacklist_count = sum(1 for _, _, r in to_delete if r == "blacklist")
dup_count = sum(1 for _, _, r in to_delete if r == "duplicate")
print(f"  blacklist: {blacklist_count}, duplicate: {dup_count}")

if "--apply" in sys.argv:
    deleted = 0
    for ev_id, title, reason in to_delete:
        try:
            svc.events().delete(calendarId="primary", eventId=ev_id).execute()
            deleted += 1
            print(f"  [{reason}] {title}")
        except Exception as e:
            print(f"  FAILED {title}: {e}")
    print(f"\nDeleted: {deleted}")
else:
    print("\nDRY RUN — pass --apply to actually delete")
    for ev_id, title, reason in to_delete[:30]:
        print(f"  [{reason}] {title}")
    if len(to_delete) > 30:
        print(f"  ... and {len(to_delete)-30} more")
