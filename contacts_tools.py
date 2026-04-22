"""
Contact Tools - Tools for saving and managing contact preferences
"""

import logging
import json
import uuid
from typing import Dict, Any, Optional
from preferences import get_preference, set_preference

# Configure logging
logger = logging.getLogger(__name__)


def normalize_nickname(nickname: str) -> str:
    """Normalize nickname: 'my husband' -> 'husband', 'MY MOM' -> 'mom'"""
    normalized = nickname.lower().strip()
    if normalized.startswith('my '):
        normalized = normalized[3:]
    return normalized


def _migrate_contacts_data(data: dict) -> dict:
    if "contacts" in data and "nickname_index" in data:
        return data
    new_contacts = {}
    new_index = {}
    for nickname, contact_data in data.items():
        contact_id = str(uuid.uuid4())
        new_contacts[contact_id] = {
            "nicknames": [nickname],
            "real_name": contact_data.get("real_name"),
            "preferred_app": contact_data.get("preferred_app"),
            "phone_numbers": contact_data.get("phone_numbers", {}),
            "primary_phone": contact_data.get("primary_phone"),
            "emails": contact_data.get("emails", {}),
            "primary_email": contact_data.get("primary_email"),
            "addresses": contact_data.get("addresses", {}),
            "primary_address": contact_data.get("primary_address"),
        }
        new_index[nickname] = contact_id
    return {"contacts": new_contacts, "nickname_index": new_index}


def _load_contacts(user_id: str) -> dict:
    contacts_json = get_preference(user_id, 'contacts')
    if not contacts_json:
        return {"contacts": {}, "nickname_index": {}}
    try:
        raw = json.loads(contacts_json) if isinstance(contacts_json, str) else contacts_json
    except json.JSONDecodeError:
        logger.warning(f"Failed to parse contacts for user {user_id}, creating new")
        return {"contacts": {}, "nickname_index": {}}
    return raw


def _save_contacts(user_id: str, data: dict) -> bool:
    return set_preference(user_id, 'contacts', json.dumps(data))


def _find_contact_by_name(data: dict, normalized_name: str):
    """Find a contact by nickname index or real_name scan. Returns (contact_id, contact_data) or (None, None)."""
    if normalized_name in data["nickname_index"]:
        contact_id = data["nickname_index"][normalized_name]
        return contact_id, data["contacts"].get(contact_id)
    for contact_id, contact in data["contacts"].items():
        real_name = contact.get("real_name", "")
        if real_name and normalize_nickname(real_name) == normalized_name:
            return contact_id, contact
    return None, None


def add_contact_preference(user_id: str, nickname: Optional[str], real_name: str, preferred_app: str,
                           phone_number: Optional[str] = None, phone_label: Optional[str] = None,
                           email: Optional[str] = None, email_label: Optional[str] = None,
                           address: Optional[str] = None, address_label: Optional[str] = None) -> Dict[str, Any]:
    """
    Add or update a contact with nickname, real name, preferred messaging app,
    and optional keyed phone numbers, emails, and addresses.

    Args:
        user_id: The user ID
        nickname: The nickname for the contact (e.g., 'husband', 'mom', 'boss').
                  If not provided, defaults to the real_name.
        real_name: The real name of the contact (e.g., 'Robin Pham')
        preferred_app: The preferred messaging app ('whatsapp' or 'sms')
        phone_number: Optional phone number (e.g., '+1234567890')
        phone_label: Label for the phone number (default 'mobile')
        email: Optional email address
        email_label: Label for the email (default 'personal')
        address: Optional postal address
        address_label: Label for the address (default 'home')

    Returns:
        Dictionary with success status and message
    """
    try:
        if not nickname:
            nickname = real_name

        normalized_nickname = normalize_nickname(nickname)
        logger.info(f"Adding contact '{normalized_nickname}' for user {user_id}")

        if preferred_app not in ['whatsapp', 'sms']:
            return {
                "success": False,
                "error": f"preferred_app must be 'whatsapp' or 'sms', got '{preferred_app}'"
            }

        if email and '@' not in email:
            return {
                "success": False,
                "error": f"Invalid email address: '{email}'"
            }

        data = _load_contacts(user_id)
        is_update = False
        contact_id = None

        # Check if nickname already exists
        if normalized_nickname in data["nickname_index"]:
            contact_id = data["nickname_index"][normalized_nickname]
            is_update = True
        else:
            # Check if real_name matches an existing contact
            for cid, contact in data["contacts"].items():
                stored_name = contact.get("real_name", "")
                if stored_name and normalize_nickname(stored_name) == normalize_nickname(real_name):
                    contact_id = cid
                    is_update = True
                    # Add this nickname to the existing contact
                    if normalized_nickname not in contact.get("nicknames", []):
                        contact["nicknames"].append(normalized_nickname)
                        data["nickname_index"][normalized_nickname] = contact_id
                    break

        if contact_id is None:
            contact_id = str(uuid.uuid4())
            data["contacts"][contact_id] = {
                "nicknames": [normalized_nickname],
                "real_name": real_name,
                "preferred_app": preferred_app,
                "phone_numbers": {},
                "primary_phone": None,
                "emails": {},
                "primary_email": None,
                "addresses": {},
                "primary_address": None,
            }
            data["nickname_index"][normalized_nickname] = contact_id

        contact = data["contacts"][contact_id]
        contact["real_name"] = real_name
        contact["preferred_app"] = preferred_app

        if phone_number:
            label = phone_label or "mobile"
            contact.setdefault("phone_numbers", {})[label] = phone_number
            if not contact.get("primary_phone"):
                contact["primary_phone"] = label

        if email:
            label = email_label or "personal"
            contact.setdefault("emails", {})[label] = email
            if not contact.get("primary_email"):
                contact["primary_email"] = label

        if address:
            label = address_label or "home"
            contact.setdefault("addresses", {})[label] = address
            if not contact.get("primary_address"):
                contact["primary_address"] = label

        success = _save_contacts(user_id, data)

        if success:
            action = "Updated" if is_update else "Added"
            logger.info(f"Successfully {action.lower()} contact '{normalized_nickname}' for user {user_id}")
            return {
                "success": True,
                "message": f"{action} contact '{normalized_nickname}' -> {real_name} ({preferred_app})"
            }
        else:
            logger.error(f"Failed to save contact preference for user {user_id}")
            return {
                "success": False,
                "error": "Failed to save contact to preferences"
            }

    except Exception as e:
        logger.error(f"Error in add_contact_preference for user {user_id}: {str(e)}", exc_info=True)
        return {
            "success": False,
            "error": f"Error adding contact: {str(e)}"
        }


async def get_contact_preference(user_id: str, name: str,
                                  websocket=None, tool_result_handler=None,
                                  conversation_id: str = None) -> Dict[str, Any]:
    """
    Look up a contact by name (can be nickname or real name) to get their details.
    If not found in saved preferences, automatically falls back to searching the
    device's native phone contacts.

    Args:
        user_id: The user ID
        name: The name to look up - can be a nickname (e.g., 'husband', 'mom') or real name (e.g., 'Robin Pham')
        websocket: Optional websocket for device contact lookup fallback
        tool_result_handler: Optional handler for device tool results
        conversation_id: Optional conversation ID for device tool context

    Returns:
        Dictionary with found status and contact details if found
    """
    try:
        normalized_name = normalize_nickname(name)
        logger.info(f"Looking up contact '{normalized_name}' for user {user_id}")

        data = _load_contacts(user_id)

        def _build_result(contact):
            return {
                "found": True,
                "nicknames": contact.get("nicknames", []),
                "real_name": contact.get("real_name"),
                "preferred_app": contact.get("preferred_app"),
                "phone_numbers": contact.get("phone_numbers", {}),
                "primary_phone": contact.get("primary_phone"),
                "emails": contact.get("emails", {}),
                "primary_email": contact.get("primary_email"),
                "addresses": contact.get("addresses", {}),
                "primary_address": contact.get("primary_address")
            }

        # Try exact nickname match via index
        if normalized_name in data["nickname_index"]:
            contact_id = data["nickname_index"][normalized_name]
            contact = data["contacts"].get(contact_id)
            if contact:
                return _build_result(contact)

        # Try exact real_name match
        for contact_id, contact in data["contacts"].items():
            real_name = contact.get("real_name", "")
            if real_name and normalize_nickname(real_name) == normalized_name:
                return _build_result(contact)

        # Try partial match: search term matches any word in real_name
        partial_matches = []
        for contact_id, contact in data["contacts"].items():
            real_name = contact.get("real_name", "")
            if real_name:
                name_words = normalize_nickname(real_name).split()
                if normalized_name in name_words:
                    partial_matches.append(contact)

        if len(partial_matches) == 1:
            return _build_result(partial_matches[0])
        elif len(partial_matches) > 1:
            return {
                "found": True,
                "multiple_matches": True,
                "contacts": [_build_result(c) for c in partial_matches],
                "message": f"Found {len(partial_matches)} saved contacts matching '{name}'."
            }

        # Not found in preferences — fall back to device phone contacts
        if websocket is not None:
            logger.info(f"Contact '{normalized_name}' not in preferences, falling back to device contacts")
            try:
                from device_control_tools import agent_lookup_phone_contacts
                device_result = await agent_lookup_phone_contacts(
                    name, user_id, websocket, tool_result_handler, conversation_id
                )
                device_contacts = device_result.get("contacts", [])
                if device_contacts:
                    logger.info(f"Found {len(device_contacts)} device contact(s) for '{name}'")
                    return {
                        "found": False,
                        "device_contacts": device_contacts,
                        "message": f"Not found in saved contacts, but found {len(device_contacts)} match(es) in phone contacts."
                    }
                elif device_result.get("permission_denied"):
                    logger.info(f"Device contacts permission denied for '{name}'")
                else:
                    logger.info(f"No device contacts found for '{name}'")
            except Exception as e:
                logger.warning(f"Device contacts fallback failed for '{name}': {e}")

        return {"found": False, "reminder": "DO NOT ask the user to create a contact unless you absolutely cannot proceed without it. For example if you have the name and messaging app you do not need to create a contact to proceed to send a message. If the user asked you for directions, just try getting directions for the phrase they gave you; the address for that phrase may already be stored in Google Maps."}

    except Exception as e:
        logger.error(f"Error in get_contact_preference for user {user_id}: {str(e)}", exc_info=True)
        return {
            "found": False,
            "error": f"Error looking up contact: {str(e)}"
        }


def list_contact_preferences(user_id: str) -> Dict[str, Any]:
    """
    List all saved contacts for a user.

    Args:
        user_id: The user ID

    Returns:
        Dictionary with list of contacts
    """
    try:
        logger.info(f"Listing contacts for user {user_id}")

        data = _load_contacts(user_id)

        contacts_list = [
            {
                "nicknames": contact.get("nicknames", []),
                "real_name": contact.get("real_name"),
                "preferred_app": contact.get("preferred_app"),
                "phone_numbers": contact.get("phone_numbers", {}),
                "primary_phone": contact.get("primary_phone"),
                "emails": contact.get("emails", {}),
                "primary_email": contact.get("primary_email"),
                "addresses": contact.get("addresses", {}),
                "primary_address": contact.get("primary_address")
            }
            for contact in data["contacts"].values()
        ]

        return {"contacts": contacts_list}

    except Exception as e:
        logger.error(f"Error in list_contact_preferences for user {user_id}: {str(e)}", exc_info=True)
        return {
            "contacts": [],
            "error": f"Error listing contacts: {str(e)}"
        }


def remove_contact_preference(user_id: str, name: str) -> Dict[str, Any]:
    """
    Delete a contact by name (can be nickname or real name).
    First checks for nickname match, then falls back to real_name match.

    Args:
        user_id: The user ID
        name: The name of the contact to delete - can be a nickname or real name

    Returns:
        Dictionary with success status
    """
    try:
        normalized_name = normalize_nickname(name)
        logger.info(f"Removing contact '{normalized_name}' for user {user_id}")

        data = _load_contacts(user_id)
        contact_id, contact = _find_contact_by_name(data, normalized_name)

        if contact_id is None:
            return {
                "success": False,
                "error": f"Contact '{normalized_name}' not found"
            }

        # Remove all nicknames from the index
        for nn in contact.get("nicknames", []):
            data["nickname_index"].pop(nn, None)

        removed_name = contact.get("real_name")
        del data["contacts"][contact_id]

        success = _save_contacts(user_id, data)

        if success:
            logger.info(f"Successfully removed contact '{normalized_name}' for user {user_id}")
            return {
                "success": True,
                "message": f"Removed contact '{normalized_name}' ({removed_name})"
            }
        else:
            logger.error(f"Failed to save contact preference after removal for user {user_id}")
            return {
                "success": False,
                "error": "Failed to save preferences after removal"
            }

    except Exception as e:
        logger.error(f"Error in remove_contact_preference for user {user_id}: {str(e)}", exc_info=True)
        return {
            "success": False,
            "error": f"Error removing contact: {str(e)}"
        }


def add_contact_nickname(user_id: str, name: str, new_nickname: str) -> Dict[str, Any]:
    """
    Add an additional nickname/alias for an existing contact.

    Args:
        user_id: The user ID
        name: Current name of the contact (existing nickname or real name)
        new_nickname: The new nickname to add
    """
    try:
        normalized_new = normalize_nickname(new_nickname)
        logger.info(f"Adding nickname '{normalized_new}' to contact '{name}' for user {user_id}")

        data = _load_contacts(user_id)

        # Check if new nickname is already in use
        if normalized_new in data["nickname_index"]:
            existing_id = data["nickname_index"][normalized_new]
            existing_contact = data["contacts"].get(existing_id, {})
            return {
                "success": False,
                "error": f"Nickname '{normalized_new}' is already assigned to contact '{existing_contact.get('real_name')}'. Remove it first or choose a different nickname."
            }

        # Find the target contact
        normalized_name = normalize_nickname(name)
        contact_id, contact = _find_contact_by_name(data, normalized_name)

        if contact_id is None:
            return {
                "success": False,
                "error": f"Contact '{name}' not found"
            }

        contact["nicknames"].append(normalized_new)
        data["nickname_index"][normalized_new] = contact_id

        success = _save_contacts(user_id, data)
        if success:
            return {
                "success": True,
                "message": f"Added nickname '{normalized_new}' to contact '{contact.get('real_name')}'. Nicknames: {contact['nicknames']}"
            }
        else:
            return {"success": False, "error": "Failed to save preferences"}

    except Exception as e:
        logger.error(f"Error in add_contact_nickname for user {user_id}: {str(e)}", exc_info=True)
        return {"success": False, "error": f"Error adding nickname: {str(e)}"}


def remove_contact_nickname(user_id: str, nickname: str) -> Dict[str, Any]:
    """
    Remove a specific nickname from a contact without deleting the contact.

    Args:
        user_id: The user ID
        nickname: The nickname to remove
    """
    try:
        normalized = normalize_nickname(nickname)
        logger.info(f"Removing nickname '{normalized}' for user {user_id}")

        data = _load_contacts(user_id)

        if normalized not in data["nickname_index"]:
            return {
                "success": False,
                "error": f"Nickname '{normalized}' not found"
            }

        contact_id = data["nickname_index"][normalized]
        contact = data["contacts"].get(contact_id)

        if not contact:
            return {"success": False, "error": f"Nickname '{normalized}' not found"}

        if len(contact.get("nicknames", [])) <= 1:
            return {
                "success": False,
                "error": f"Cannot remove the last nickname. Use remove_contact_preference to delete the entire contact."
            }

        contact["nicknames"].remove(normalized)
        del data["nickname_index"][normalized]

        success = _save_contacts(user_id, data)
        if success:
            return {
                "success": True,
                "message": f"Removed nickname '{normalized}' from contact '{contact.get('real_name')}'. Remaining nicknames: {contact['nicknames']}"
            }
        else:
            return {"success": False, "error": "Failed to save preferences"}

    except Exception as e:
        logger.error(f"Error in remove_contact_nickname for user {user_id}: {str(e)}", exc_info=True)
        return {"success": False, "error": f"Error removing nickname: {str(e)}"}


# Define the contact tools for Claude
contacts_tools = [
    {
        "type": "custom",
        "name": "add_contact_preference",
        "description": "Add or update a contact with real name, nickname, preferred messaging app, and optional phone number, email, and address. Each phone/email/address is stored with a label (e.g. 'mobile', 'work', 'personal', 'home'). If the user doesn't provide phone/email/address details, consider using agent_lookup_phone_contacts first to auto-fill from their phone contacts.",
        "input_schema": {
            "type": "object",
            "properties": {
                "nickname": {
                    "type": "string",
                    "description": "The nickname for the contact (e.g., 'husband', 'mom', 'boss'). Optional - if not provided, real_name will be used as the nickname."
                },
                "real_name": {
                    "type": "string",
                    "description": "The real name of the contact (e.g., 'Robin Pham', 'Jane Doe')"
                },
                "preferred_app": {
                    "type": "string",
                    "enum": ["whatsapp", "sms"],
                    "description": "The preferred messaging app for this contact"
                },
                "phone_number": {
                    "type": "string",
                    "description": "A phone number for the contact (e.g., '+1234567890', '555-1234')"
                },
                "phone_label": {
                    "type": "string",
                    "description": "Label for the phone number (e.g., 'mobile', 'work', 'home'). Defaults to 'mobile'."
                },
                "email": {
                    "type": "string",
                    "description": "An email address for the contact"
                },
                "email_label": {
                    "type": "string",
                    "description": "Label for the email (e.g., 'personal', 'work'). Defaults to 'personal'."
                },
                "address": {
                    "type": "string",
                    "description": "A postal address for the contact"
                },
                "address_label": {
                    "type": "string",
                    "description": "Label for the address (e.g., 'home', 'work'). Defaults to 'home'."
                }
            },
            "required": ["real_name", "preferred_app"]
        }
    },
    {
        "type": "custom",
        "name": "get_contact_preference",
        "description": "Look up a contact by name to get their real name, preferred messaging app, phone numbers, email addresses, and postal addresses. Use this BEFORE sending messages. Do NOT use this for searching addresses for agent_get_google_maps_directions unless the query involves someone's name. This automatically searches saved contacts first, then falls back to the device's native phone contacts if not found. If the result includes 'device_contacts', those are matches from the phone — you can use that info directly or suggest saving it. If the contact isn't found anywhere and you have the info you need (e.g. name and messaging app), send the message anyway. If the contact IS found, make SURE you have used the correct contact name (not the nickname) for sending messages. Also use this to look up a contact's email for Asana task assignment. The name can match either the nickname or the real name.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "The name to look up - can be a nickname (e.g., 'husband', 'mom', 'boss') or a real name (e.g., 'Robin Pham')"
                }
            },
            "required": ["name"]
        }
    },
    {
        "type": "custom",
        "name": "list_contact_preferences",
        "description": "List all saved contacts with their nicknames, real names, preferred messaging apps, phone numbers, email addresses, and postal addresses.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "type": "custom",
        "name": "remove_contact_preference",
        "description": "Delete an entire saved contact (all nicknames) by name. The name can be a nickname or real name. To remove just one nickname alias without deleting the contact, use remove_contact_nickname instead.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "The name of the contact to remove - can be a nickname (e.g., 'husband', 'mom', 'boss') or a real name (e.g., 'Robin Pham')"
                }
            },
            "required": ["name"]
        }
    },
    {
        "type": "custom",
        "name": "add_contact_nickname",
        "description": "Add an additional nickname/alias for an existing contact. Use this when the user wants to refer to an existing contact by a new name.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "The current name of the contact (existing nickname or real name) to identify which contact to update"
                },
                "new_nickname": {
                    "type": "string",
                    "description": "The new nickname/alias to add for this contact"
                }
            },
            "required": ["name", "new_nickname"]
        }
    },
    {
        "type": "custom",
        "name": "remove_contact_nickname",
        "description": "Remove a specific nickname/alias from a contact without deleting the contact itself. Cannot remove the last remaining nickname — use remove_contact_preference to delete the whole contact instead.",
        "input_schema": {
            "type": "object",
            "properties": {
                "nickname": {
                    "type": "string",
                    "description": "The nickname to remove from the contact"
                }
            },
            "required": ["nickname"]
        }
    }
]
