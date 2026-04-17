"""Write contact notes to Google Drive as markdown files."""
import logging

from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload

from common.google_auth import get_credentials

logger = logging.getLogger(__name__)

CONTACTS_FOLDER_NAME = "Contatti"
_folder_id_cache: str | None = None


def _build_service():
    return build("drive", "v3", credentials=get_credentials())


def _get_or_create_folder(service) -> str:
    global _folder_id_cache
    if _folder_id_cache:
        return _folder_id_cache

    result = service.files().list(
        q=f"name='{CONTACTS_FOLDER_NAME}' and mimeType='application/vnd.google-apps.folder' and trashed=false",
        fields="files(id)",
        pageSize=1,
    ).execute()

    files = result.get("files", [])
    if files:
        _folder_id_cache = files[0]["id"]
    else:
        folder = service.files().create(
            body={"name": CONTACTS_FOLDER_NAME, "mimeType": "application/vnd.google-apps.folder"},
            fields="id",
        ).execute()
        _folder_id_cache = folder["id"]
        logger.info("Created Drive folder: %s", CONTACTS_FOLDER_NAME)

    return _folder_id_cache


def _file_url(file_id: str) -> str:
    return f"https://drive.google.com/file/d/{file_id}/view"


def append_contact_note(contact_name: str, full_notes: str, notes_url: str = "") -> str:
    """
    Create or overwrite the markdown file for a contact with the full notes text.
    full_notes should already contain the complete accumulated text (including new note).
    Returns the Drive file URL.
    """
    service = _build_service()
    folder_id = _get_or_create_folder(service)
    filename = f"{contact_name}.md"
    content = full_notes.encode("utf-8")
    media = MediaInMemoryUpload(content, mimetype="text/markdown", resumable=False)

    # Check if file already exists (by URL hint or by name search)
    file_id = None
    if notes_url:
        # Extract file_id from URL: .../file/d/{id}/view
        parts = notes_url.rstrip("/").split("/")
        try:
            idx = parts.index("d")
            file_id = parts[idx + 1]
        except (ValueError, IndexError):
            pass

    if not file_id:
        result = service.files().list(
            q=f"name='{filename}' and '{folder_id}' in parents and trashed=false",
            fields="files(id)",
            pageSize=1,
        ).execute()
        files = result.get("files", [])
        if files:
            file_id = files[0]["id"]

    if file_id:
        service.files().update(fileId=file_id, media_body=media).execute()
        logger.info("Updated Drive notes: %s", filename)
    else:
        created = service.files().create(
            body={"name": filename, "parents": [folder_id]},
            media_body=media,
            fields="id",
        ).execute()
        file_id = created["id"]
        logger.info("Created Drive notes: %s", filename)

    return _file_url(file_id)
