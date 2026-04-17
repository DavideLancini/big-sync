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
    """Return the Drive folder ID for CONTACTS_FOLDER_NAME, creating it if needed."""
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
            body={
                "name": CONTACTS_FOLDER_NAME,
                "mimeType": "application/vnd.google-apps.folder",
            },
            fields="id",
        ).execute()
        _folder_id_cache = folder["id"]
        logger.info("Created Drive folder: %s", CONTACTS_FOLDER_NAME)

    return _folder_id_cache


def _file_url(file_id: str) -> str:
    return f"https://drive.google.com/file/d/{file_id}/view"


def upsert_notes_file(contact_name: str, notes: str) -> str:
    """
    Create or update a markdown file in the Contatti folder for the given contact.
    Returns the public URL of the file.
    """
    service = _build_service()
    folder_id = _get_or_create_folder(service)
    filename = f"{contact_name}.md"
    content = notes.encode("utf-8")
    media = MediaInMemoryUpload(content, mimetype="text/markdown", resumable=False)

    # Check if file already exists
    result = service.files().list(
        q=f"name='{filename}' and '{folder_id}' in parents and trashed=false",
        fields="files(id)",
        pageSize=1,
    ).execute()
    files = result.get("files", [])

    if files:
        file_id = files[0]["id"]
        service.files().update(fileId=file_id, media_body=media).execute()
        logger.info("Updated Drive notes file: %s", filename)
    else:
        file_meta = {"name": filename, "parents": [folder_id]}
        created = service.files().create(
            body=file_meta, media_body=media, fields="id"
        ).execute()
        file_id = created["id"]
        logger.info("Created Drive notes file: %s", filename)

    return _file_url(file_id)
