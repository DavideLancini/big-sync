"""
Minimal Python port of applaud's reverse-engineered Plaud cloud client.

Auth: Bearer JWT extracted once from web.plaud.ai localStorage (key 'tokenstr').
Token valid ~10 months. Set PLAUD_TOKEN in .env.
"""
import logging
from typing import Optional
from urllib.parse import urlencode

import requests
from decouple import config

logger = logging.getLogger(__name__)

_REGION_API_BASES = {
    "aws:us-west-2":     "https://api.plaud.ai",
    "aws:eu-central-1":  "https://api-euc1.plaud.ai",
    "aws:ap-southeast-1": "https://api-apse1.plaud.ai",
}
_DEFAULT_BASE = "https://api.plaud.ai"
_USER_AGENT = "big-sync/1.0 (plaud-client)"

_BASE_TO_REGION = {
    base.replace("https://", ""): region for region, base in _REGION_API_BASES.items()
}


class PlaudAuthError(Exception):
    pass


class PlaudApiError(Exception):
    def __init__(self, message: str, status: int = 0, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


def _token() -> str:
    tok = config("PLAUD_TOKEN", default="").strip()
    if not tok:
        raise PlaudAuthError("PLAUD_TOKEN missing in .env")
    return tok


def _region() -> str:
    return config("PLAUD_REGION", default="aws:us-west-2").strip() or "aws:us-west-2"


def _api_base() -> str:
    return _REGION_API_BASES.get(_region(), _DEFAULT_BASE)


def _resolve_region_from_domain(api_url: str) -> Optional[str]:
    try:
        host = api_url.replace("https://", "").split("/")[0]
        return _BASE_TO_REGION.get(host)
    except Exception:
        return None


def _request(method: str, path: str, *, params=None, timeout: int = 30):
    url = path if path.startswith("http") else f"{_api_base()}{path}"
    if params:
        url = f"{url}?{urlencode(params)}"
    headers = {
        "accept": "application/json",
        "user-agent": _USER_AGENT,
        "authorization": f"Bearer {_token()}",
    }

    last_err = None
    for attempt in range(1, 4):
        try:
            res = requests.request(method, url, headers=headers, timeout=timeout)
            if res.status_code == 401:
                raise PlaudAuthError("Plaud 401 — token expired or revoked")
            if res.status_code >= 500 and attempt < 3:
                logger.warning("plaud %s %s → %d (attempt %d)", method, path, res.status_code, attempt)
                continue
            return res
        except requests.RequestException as e:
            last_err = e
            if attempt < 3:
                continue
    raise PlaudApiError(f"network error after 3 attempts: {last_err}")


def _json(method: str, path: str, *, params=None):
    """GET/POST JSON, with Plaud's region-mismatch (-302) auto-correction."""
    res = _request(method, path, params=params)
    text = res.text
    if not res.ok:
        raise PlaudApiError(f"Plaud {method} {path} → {res.status_code}", res.status_code, text[:500])

    try:
        body = res.json()
    except ValueError:
        raise PlaudApiError(f"Plaud {path} non-JSON: {text[:200]}", res.status_code, text[:500])

    # Region mismatch: status -302, returns correct domain to use.
    if isinstance(body, dict) and body.get("status") == -302:
        domains = (body.get("data") or {}).get("domains") or {}
        correct_domain = domains.get("api")
        if not correct_domain:
            raise PlaudApiError(f"Plaud region mismatch with no api domain: {body}")
        new_region = _resolve_region_from_domain(correct_domain)
        if not new_region:
            raise PlaudApiError(f"Plaud region mismatch: unknown domain {correct_domain}")
        logger.info("plaud region mismatch → set PLAUD_REGION=%s in .env (was %s)",
                    new_region, _region())
        # Retry once against the corrected base (in-memory only).
        retry_url = f"{_REGION_API_BASES[new_region]}{path}"
        if params:
            retry_url = f"{retry_url}?{urlencode(params)}"
        res2 = requests.request(method, retry_url, headers={
            "accept": "application/json",
            "user-agent": _USER_AGENT,
            "authorization": f"Bearer {_token()}",
        }, timeout=30)
        if not res2.ok:
            raise PlaudApiError(f"Plaud retry {path} → {res2.status_code}", res2.status_code, res2.text[:500])
        return res2.json()

    return body


def list_recordings(skip: int = 0, limit: int = 50) -> list[dict]:
    """Return a page of recordings (newest first by start_time)."""
    body = _json("GET", "/file/simple/web", params={
        "skip": skip,
        "limit": limit,
        "is_trash": 2,
        "sort_by": "start_time",
        "is_desc": "true",
    })
    return body.get("data_file_list") or []


def list_all(page_size: int = 50, max_pages: int = 200) -> list[dict]:
    out: list[dict] = []
    skip = 0
    for _ in range(max_pages):
        page = list_recordings(skip=skip, limit=page_size)
        if not page:
            break
        out.extend(page)
        if len(page) < page_size:
            break
        skip += page_size
    return out


def get_audio_url(recording_id: str) -> str:
    body = _json("GET", f"/file/temp-url/{recording_id}")
    url = body.get("temp_url")
    if not url:
        raise PlaudApiError(f"no temp_url for {recording_id}: {body}")
    return url


def download_audio(recording_id: str, dest_path: str) -> int:
    """Stream presigned-S3 audio to disk. Returns bytes written."""
    url = get_audio_url(recording_id)
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        total = 0
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=64 * 1024):
                if chunk:
                    f.write(chunk)
                    total += len(chunk)
    return total
