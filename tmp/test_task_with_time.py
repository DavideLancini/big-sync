"""Test: create a Google Task with start+end time (15min duration) tomorrow at current hour."""
import os
import sys
from datetime import datetime, timedelta

import django

sys.path.insert(0, "/var/www/big-sync")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from googleapiclient.discovery import build
from common.google_auth import get_credentials

service = build("tasks", "v1", credentials=get_credentials())

TASKLIST_TITLE = "big-sync"
tasklists = service.tasklists().list().execute()
tasklist_id = None
for tl in tasklists.get("items", []):
    if tl.get("title") == TASKLIST_TITLE:
        tasklist_id = tl["id"]
        break
if not tasklist_id:
    tl = service.tasklists().insert(body={"title": TASKLIST_TITLE}).execute()
    tasklist_id = tl["id"]

now = datetime.now()
start = (now + timedelta(days=1)).replace(second=0, microsecond=0)
end = start + timedelta(minutes=15)

body = {
    "title": f"[TEST] Task con orario {start.strftime('%H:%M')}-{end.strftime('%H:%M')}",
    "notes": "Test script: start+end time support.",
    "due": start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
}

print(f"Inserting task: start={start.isoformat()} end={end.isoformat()}")
print(f"Body: {body}")

result = service.tasks().insert(tasklist=tasklist_id, body=body).execute()

print("\n=== API RESPONSE ===")
import json
print(json.dumps(result, indent=2, default=str))

print("\n=== READ BACK ===")
read = service.tasks().get(tasklist=tasklist_id, task=result["id"]).execute()
print(json.dumps(read, indent=2, default=str))

print("\nTask id:", result["id"])
print("Self link:", result.get("selfLink"))
print("\nFields present:", sorted(result.keys()))
