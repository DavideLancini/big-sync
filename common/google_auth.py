"""Google OAuth2 credentials from env vars."""
from decouple import config
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

SCOPES_CONTACTS = ["https://www.googleapis.com/auth/contacts"]
SCOPES_CALENDAR = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
]
SCOPES_TASKS = ["https://www.googleapis.com/auth/tasks"]


def get_credentials(service: str = "contacts") -> Credentials:
    """
    Return refreshed credentials for the given service.
    service: 'contacts' | 'calendar' | 'tasks'
    """
    if service == "contacts":
        refresh_token = config("GOOGLE_REFRESH_TOKEN_CONTACTS")
        scopes = SCOPES_CONTACTS
    elif service == "calendar":
        refresh_token = config("GOOGLE_REFRESH_TOKEN_CALENDAR")
        scopes = SCOPES_CALENDAR
    elif service == "tasks":
        # Falls back to GOOGLE_REFRESH_TOKEN_TASKS if set, else calendar token.
        refresh_token = config("GOOGLE_REFRESH_TOKEN_TASKS",
                               default=config("GOOGLE_REFRESH_TOKEN_CALENDAR"))
        scopes = SCOPES_TASKS
    else:
        raise ValueError(f"Unknown service: {service}")

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=config("GOOGLE_CLIENT_ID"),
        client_secret=config("GOOGLE_CLIENT_SECRET"),
        scopes=scopes,
    )
    creds.refresh(Request())
    return creds
