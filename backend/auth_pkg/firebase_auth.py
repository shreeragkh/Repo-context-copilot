"""
Firebase Authentication module.

Verifies Firebase ID tokens using Google's public keys (no service account
needed) and manages lightweight server-side sessions. Only ADMIN_EMAIL is
allowed to actually become an authenticated session - everyone else gets a
"public" (unauthenticated) chat experience.
"""
import uuid
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from google.oauth2 import id_token as google_id_token
from google.auth.transport import requests as google_requests

from config import settings

logger = logging.getLogger(__name__)

# In-memory session store (single-process; fine for this app's scale).
_sessions: dict[str, dict[str, Any]] = {}


def verify_firebase_token(token: str) -> dict[str, Any]:
    """Verify a Firebase ID token using Google's public keys.
    Returns decoded claims on success, raises ValueError otherwise."""
    try:
        claims = google_id_token.verify_firebase_token(
            token,
            google_requests.Request(),
            audience=settings.FIREBASE_PROJECT_ID,
        )
        logger.info(
            "Firebase token verified", extra={"component": "auth",
            "detail": f"email={claims.get('email')} uid={claims.get('sub')}"},
        )
        return claims
    except Exception as e:
        logger.warning("Firebase token verification failed: %s", e, extra={"component": "auth"})
        raise ValueError(f"Invalid Firebase token: {e}") from e


def is_admin(email: str) -> bool:
    return email.strip().lower() == settings.ADMIN_EMAIL.strip().lower()


def create_session(email: str, uid: str, display_name: str = "", photo_url: str = "") -> str:
    session_id = uuid.uuid4().hex
    _sessions[session_id] = {
        "email": email,
        "uid": uid,
        "display_name": display_name,
        "photo_url": photo_url,
        "is_admin": is_admin(email),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(),
    }
    logger.info("Session created", extra={"component": "auth", "detail": f"admin={is_admin(email)}"})
    return session_id


def get_session(session_id: str) -> dict[str, Any] | None:
    session = _sessions.get(session_id)
    if session is None:
        return None
    expires = datetime.fromisoformat(session["expires_at"])
    if datetime.now(timezone.utc) > expires:
        del _sessions[session_id]
        return None
    return session


def delete_session(session_id: str) -> bool:
    return _sessions.pop(session_id, None) is not None
