"""Write todos to Google Tasks."""
import logging
import re
from datetime import datetime

from googleapiclient.discovery import build

from common.google_auth import get_credentials
from common.models import WriteLog

logger = logging.getLogger(__name__)

_TASKLIST_TITLE = "big-sync"
_tasklist_id_cache: str | None = None


def _build_service():
    return build("tasks", "v1", credentials=get_credentials())


def _get_or_create_tasklist(service) -> str:
    """Return the id of the 'big-sync' tasklist, creating it if needed."""
    global _tasklist_id_cache
    if _tasklist_id_cache:
        return _tasklist_id_cache

    result = service.tasklists().list().execute()
    for tl in result.get("items", []):
        if tl.get("title") == _TASKLIST_TITLE:
            _tasklist_id_cache = tl["id"]
            return _tasklist_id_cache

    # Create it
    tl = service.tasklists().insert(body={"title": _TASKLIST_TITLE}).execute()
    _tasklist_id_cache = tl["id"]
    logger.info("Created tasklist: %s", _TASKLIST_TITLE)
    return _tasklist_id_cache


def _normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", (title or "").lower().strip())


def _find_existing(service, tasklist_id: str, title: str) -> dict | None:
    """Search for a task with the same title (case-insensitive)."""
    if not title:
        return None
    norm = _normalize_title(title)
    try:
        result = service.tasks().list(
            tasklist=tasklist_id,
            showCompleted=False,
            showHidden=False,
        ).execute()
        for task in result.get("items", []):
            if _normalize_title(task.get("title", "")) == norm:
                return task
    except Exception:
        logger.exception("Error searching tasks: %s", title)
    return None


def _due_rfc3339(date_str: str) -> str | None:
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").strftime("%Y-%m-%dT00:00:00.000Z")
    except ValueError:
        return None


def upsert_task(data: dict) -> str | None:
    """
    Create or skip-if-exists a Google Task.
    Returns the task id or None on error.
    data keys: title, due_date, notes, assigned_to
    """
    title = (data.get("title") or "").strip()
    if not title:
        return None

    # Only create tasks assigned to me
    assigned = (data.get("assigned_to") or "me").lower().strip()
    if assigned not in ("me", "davide", "davide lancini", "@davidelenc"):
        logger.debug("Skipping task assigned to %s: %s", assigned, title)
        return None

    service = _build_service()
    tasklist_id = _get_or_create_tasklist(service)

    existing = _find_existing(service, tasklist_id, title)
    if existing:
        task_id = existing["id"]
        # Enrich: add notes if missing
        if data.get("notes") and not existing.get("notes"):
            try:
                service.tasks().patch(
                    tasklist=tasklist_id,
                    task=task_id,
                    body={"notes": data["notes"]},
                ).execute()
                logger.info("Enriched task: %s", title)
            except Exception:
                logger.exception("Error enriching task: %s", task_id)
        else:
            logger.debug("Task already exists: %s", title)
        return task_id

    body: dict = {"title": title}
    if data.get("notes"):
        body["notes"] = data["notes"]
    due = _due_rfc3339(data.get("due_date") or "")
    if due:
        body["due"] = due

    try:
        result = service.tasks().insert(tasklist=tasklist_id, body=body).execute()
        logger.info("Created task: %s", title)
        WriteLog.objects.create(type=WriteLog.TYPE_TASK, title=title, detail=data.get("due_date") or "")
        return result.get("id")
    except Exception:
        logger.exception("Error creating task: %s", data)
        return None
