"""
One-time Google OAuth2 setup.

Usage:
    python scripts/google_oauth_setup.py

Steps:
    1. Opens an authorization URL in your browser (or prints it).
    2. You log in with davidelenc@gmail.com and grant access.
    3. Google redirects to localhost — paste the full redirect URL here.
    4. Script prints GOOGLE_REFRESH_TOKEN to add to .env on the server.

Requires GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in your local .env.
"""
import sys
import urllib.parse
import urllib.request
import json
import webbrowser
from decouple import config

SCOPES = " ".join([
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/contacts",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/drive.file",
])

REDIRECT_URI = "http://localhost"


def main():
    client_id = config("GOOGLE_CLIENT_ID")
    client_secret = config("GOOGLE_CLIENT_SECRET")

    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",
        "prompt": "consent",  # forces refresh_token to be returned
    }
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)

    print("\n=== Google OAuth2 Setup ===\n")
    print("Opening authorization URL in browser...")
    print("Log in with davidelenc@gmail.com and grant all requested permissions.\n")
    print(auth_url)
    webbrowser.open(auth_url)

    print("\nAfter granting access, your browser will redirect to localhost")
    print("(the page will show an error — that's fine).")
    redirect_url = input("\nPaste the full redirect URL here: ").strip()

    parsed = urllib.parse.urlparse(redirect_url)
    code = urllib.parse.parse_qs(parsed.query).get("code", [None])[0]
    if not code:
        print("ERROR: no 'code' found in URL")
        sys.exit(1)

    # Exchange code for tokens
    data = urllib.parse.urlencode({
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    }).encode()

    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req) as resp:
        tokens = json.loads(resp.read())

    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        print("ERROR: no refresh_token in response:", tokens)
        sys.exit(1)

    # Verify account
    access_token = tokens["access_token"]
    req2 = urllib.request.Request(
        "https://www.googleapis.com/oauth2/v2/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    with urllib.request.urlopen(req2) as resp:
        userinfo = json.loads(resp.read())

    print(f"\n✓ Authorized as: {userinfo.get('email')} ({userinfo.get('name')})")
    print(f"\nAdd this to .env on the server:\n")
    print(f"GOOGLE_REFRESH_TOKEN={refresh_token}")
    print(f"\nRun on server:")
    print(f"  ssh elisabetta \"sed -i 's|^GOOGLE_REFRESH_TOKEN=.*|GOOGLE_REFRESH_TOKEN={refresh_token}|' /var/www/big-sync/.env\"")


if __name__ == "__main__":
    main()
