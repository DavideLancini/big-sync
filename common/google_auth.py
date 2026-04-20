"""Google OAuth2 credentials from env vars."""
from decouple import config
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

SCOPES = [
    "https://www.googleapis.com/auth/contacts",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/cloud-billing.readonly",
]


def get_credentials() -> Credentials:
    creds = Credentials(
        token=None,
        refresh_token=config("GOOGLE_REFRESH_TOKEN"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=config("GOOGLE_CLIENT_ID"),
        client_secret=config("GOOGLE_CLIENT_SECRET"),
        scopes=SCOPES,
    )
    creds.refresh(Request())
    return creds
