"""
Google Contacts lookup via the People API.

Third (lowest-priority) contact source, behind app-saved contacts and the
device's phone contacts. Used as a fallback inside
`contacts_tools.get_contact_preference`.

Requires a per-user OAuth refresh token (with the contacts read-only scopes)
that was captured at sign-in and stored encrypted under the
`google_contacts_refresh_token` preference key. If no token is stored, or any
People API call fails, every function here degrades silently to "no match" —
it must never raise into the contact-lookup chain.
"""

import logging
import os

import requests

from preferences import get_decrypted_preference_key, set_encrypted_preference_key

try:
    from constants import GOOGLE_WEB_CLIENT_SECRET
except ImportError:
    GOOGLE_WEB_CLIENT_SECRET = os.getenv("GOOGLE_WEB_CLIENT_SECRET", "")

logger = logging.getLogger(__name__)

# Web OAuth client ID (same one used for ID-token verification in auth.py).
GOOGLE_WEB_CLIENT_ID = "2815827813-se3l1u83nqbtda59dtplcbbjsr38oqln.apps.googleusercontent.com"

TOKEN_URI = "https://oauth2.googleapis.com/token"

# Scopes requested at sign-in. contacts.readonly -> myContacts (people.searchContacts),
# contacts.other.readonly -> otherContacts (Gmail auto-saved).
GOOGLE_CONTACTS_SCOPES = [
    "https://www.googleapis.com/auth/contacts.readonly",
    "https://www.googleapis.com/auth/contacts.other.readonly",
]

REFRESH_TOKEN_PREF_KEY = "google_contacts_refresh_token"

# Fields we request from each source. otherContacts only supports
# names/emailAddresses/phoneNumbers/metadata (no addresses).
_MY_CONTACTS_READ_MASK = "names,phoneNumbers,emailAddresses,addresses"
_OTHER_CONTACTS_READ_MASK = "names,phoneNumbers,emailAddresses"


def store_refresh_token_from_auth_code(user_id, auth_code):
    """Exchange a one-time Google server auth code for a refresh token and store it.

    Called at login when the client supplies a serverAuthCode. Returns True on
    success. Never raises — a failure here must not block sign-in, so the caller
    can ignore the result.
    """
    if not auth_code:
        return False
    try:
        # Auth codes obtained via Android's requestServerAuthCode are redeemed with
        # an empty redirect_uri.
        resp = requests.post(
            TOKEN_URI,
            data={
                "code": auth_code,
                "client_id": GOOGLE_WEB_CLIENT_ID,
                "client_secret": GOOGLE_WEB_CLIENT_SECRET,
                "redirect_uri": "",
                "grant_type": "authorization_code",
            },
            timeout=10,
        )
        if resp.status_code != 200:
            logger.warning(
                f"Google auth-code exchange failed for user {user_id}: "
                f"{resp.status_code} {resp.text[:200]}"
            )
            return False
        refresh_token = resp.json().get("refresh_token")
        if not refresh_token:
            # Google only returns a refresh token when offline access is granted
            # (requestServerAuthCode with forceCodeForRefreshToken=true ensures this).
            logger.warning(f"No refresh_token in Google exchange response for user {user_id}")
            return False
        set_encrypted_preference_key(user_id, REFRESH_TOKEN_PREF_KEY, refresh_token)
        logger.info(f"Stored Google contacts refresh token for user {user_id}")
        return True
    except Exception as e:
        logger.warning(f"Error exchanging Google auth code for user {user_id}: {e}")
        return False


def has_google_contacts_token(user_id):
    """True if a Google contacts refresh token is stored for the user."""
    return bool(get_decrypted_preference_key(user_id, REFRESH_TOKEN_PREF_KEY))


def _build_people_service(user_id):
    """Build an authorized People API client for the user, or None if unavailable.

    Returns None when the user has no stored refresh token (e.g. they signed in
    before the contacts scopes were added and haven't re-consented), or when the
    Google client libraries / credentials can't be initialized.
    """
    refresh_token = get_decrypted_preference_key(user_id, REFRESH_TOKEN_PREF_KEY)
    if not refresh_token:
        logger.info(f"No Google contacts refresh token stored for user {user_id}; skipping Google Contacts")
        return None

    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri=TOKEN_URI,
            client_id=GOOGLE_WEB_CLIENT_ID,
            client_secret=GOOGLE_WEB_CLIENT_SECRET,
            scopes=GOOGLE_CONTACTS_SCOPES,
        )
        # cache_discovery=False avoids noisy oauth2client cache warnings on servers.
        return build("people", "v1", credentials=creds, cache_discovery=False)
    except Exception as e:
        logger.warning(f"Could not build People API service for user {user_id}: {e}")
        return None


def _label_dict(entries, value_key="value"):
    """Turn a list of People API typed entries into a {label: value} dict.

    Mirrors the shape the device phone-contacts fallback returns. Collisions on
    the same label get a numeric suffix so no value is dropped.
    """
    result = {}
    for entry in entries or []:
        value = entry.get(value_key) or entry.get("formattedValue")
        if not value:
            continue
        label = (entry.get("type") or entry.get("formattedType") or "other").lower()
        key = label
        n = 2
        while key in result:
            key = f"{label}_{n}"
            n += 1
        result[key] = value
    return result


def _person_to_contact(person):
    """Normalize a People API `person` into the device-contact shape, or None."""
    names = person.get("names") or []
    display_name = names[0].get("displayName") if names else None

    contact = {
        "display_name": display_name,
        "phone_numbers": _label_dict(person.get("phoneNumbers")),
        "emails": _label_dict(person.get("emailAddresses")),
        "addresses": _label_dict(person.get("addresses"), value_key="formattedValue"),
    }

    # Skip entries with nothing useful to act on.
    if not contact["display_name"] and not contact["phone_numbers"] and not contact["emails"]:
        return None
    return contact


def lookup_google_contacts(user_id, name):
    """Search a user's Google Contacts (saved + auto-saved) by name.

    Returns {"contacts": [<device-shaped contact>, ...]} (possibly empty).
    Never raises.
    """
    service = _build_people_service(user_id)
    if service is None:
        return {"contacts": []}

    contacts = []
    seen_resource_names = set()

    def _collect(results):
        for result in results or []:
            person = result.get("person") or {}
            resource_name = person.get("resourceName")
            if resource_name and resource_name in seen_resource_names:
                continue
            if resource_name:
                seen_resource_names.add(resource_name)
            normalized = _person_to_contact(person)
            if normalized:
                contacts.append(normalized)

    # Saved contacts (myContacts).
    try:
        my_result = service.people().searchContacts(
            query=name, readMask=_MY_CONTACTS_READ_MASK
        ).execute()
        _collect(my_result.get("results"))
    except Exception as e:
        logger.warning(f"Google myContacts search failed for user {user_id}: {e}")

    # Auto-saved contacts (otherContacts, e.g. from Gmail).
    try:
        other_result = service.otherContacts().search(
            query=name, readMask=_OTHER_CONTACTS_READ_MASK
        ).execute()
        _collect(other_result.get("results"))
    except Exception as e:
        logger.warning(f"Google otherContacts search failed for user {user_id}: {e}")

    if contacts:
        logger.info(f"Found {len(contacts)} Google contact(s) for '{name}' (user {user_id})")
        for c in contacts:
            logger.info(
                f"  Google contact: name={c.get('display_name')!r} "
                f"phones={list((c.get('phone_numbers') or {}).keys())} "
                f"emails={list((c.get('emails') or {}).keys())} "
                f"addresses={list((c.get('addresses') or {}).keys())}"
            )
    return {"contacts": contacts}
