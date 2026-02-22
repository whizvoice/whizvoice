"""
Contact Tools - Tools for saving and managing contact preferences
"""

import logging
import json
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
        # Use real_name as nickname if nickname not provided
        if not nickname:
            nickname = real_name

        # Normalize the nickname
        normalized_nickname = normalize_nickname(nickname)

        logger.info(f"Adding contact '{normalized_nickname}' for user {user_id}")

        # Validate preferred_app
        if preferred_app not in ['whatsapp', 'sms']:
            return {
                "success": False,
                "error": f"preferred_app must be 'whatsapp' or 'sms', got '{preferred_app}'"
            }

        # Validate email if provided
        if email and '@' not in email:
            return {
                "success": False,
                "error": f"Invalid email address: '{email}'"
            }

        # Get existing contacts from preferences
        contacts_json = get_preference(user_id, 'contacts')
        if contacts_json:
            try:
                contacts = json.loads(contacts_json) if isinstance(contacts_json, str) else contacts_json
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse existing contacts for user {user_id}, creating new")
                contacts = {}
        else:
            contacts = {}

        # Add/update the contact
        is_update = normalized_nickname in contacts

        # Preserve existing keyed fields when updating
        existing_contact = contacts.get(normalized_nickname, {})
        existing_phone_numbers = existing_contact.get("phone_numbers", {})
        existing_emails = existing_contact.get("emails", {})
        existing_addresses = existing_contact.get("addresses", {})
        existing_primary_phone = existing_contact.get("primary_phone")
        existing_primary_email = existing_contact.get("primary_email")
        existing_primary_address = existing_contact.get("primary_address")

        contacts[normalized_nickname] = {
            "real_name": real_name,
            "preferred_app": preferred_app
        }

        # Handle phone number
        if phone_number:
            label = phone_label or "mobile"
            existing_phone_numbers[label] = phone_number
            if not existing_primary_phone:
                existing_primary_phone = label
        if existing_phone_numbers:
            contacts[normalized_nickname]["phone_numbers"] = existing_phone_numbers
            contacts[normalized_nickname]["primary_phone"] = existing_primary_phone

        # Handle email (keyed format)
        if email:
            label = email_label or "personal"
            existing_emails[label] = email
            if not existing_primary_email:
                existing_primary_email = label
        if existing_emails:
            contacts[normalized_nickname]["emails"] = existing_emails
            contacts[normalized_nickname]["primary_email"] = existing_primary_email

        # Handle address
        if address:
            label = address_label or "home"
            existing_addresses[label] = address
            if not existing_primary_address:
                existing_primary_address = label
        if existing_addresses:
            contacts[normalized_nickname]["addresses"] = existing_addresses
            contacts[normalized_nickname]["primary_address"] = existing_primary_address

        # Save back to preferences
        success = set_preference(user_id, 'contacts', json.dumps(contacts))

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
        # Normalize the search name
        normalized_name = normalize_nickname(name)

        logger.info(f"Looking up contact '{normalized_name}' for user {user_id}")

        # Get contacts from preferences
        contacts_json = get_preference(user_id, 'contacts')
        contacts = {}
        if contacts_json:
            try:
                contacts = json.loads(contacts_json) if isinstance(contacts_json, str) else contacts_json
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse contacts for user {user_id}")

        def _build_result(nickname, contact):
            return {
                "found": True,
                "nickname": nickname,
                "real_name": contact.get("real_name"),
                "preferred_app": contact.get("preferred_app"),
                "phone_numbers": contact.get("phone_numbers", {}),
                "primary_phone": contact.get("primary_phone"),
                "emails": contact.get("emails", {}),
                "primary_email": contact.get("primary_email"),
                "addresses": contact.get("addresses", {}),
                "primary_address": contact.get("primary_address")
            }

        # First, try to match by nickname (key)
        if normalized_name in contacts:
            return _build_result(normalized_name, contacts[normalized_name])

        # If not found by nickname, try to match by real_name
        for nickname, contact in contacts.items():
            real_name = contact.get("real_name", "")
            if real_name and normalize_nickname(real_name) == normalized_name:
                return _build_result(nickname, contact)

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
                    # Fall through to the not-found response
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

        # Get contacts from preferences
        contacts_json = get_preference(user_id, 'contacts')
        if not contacts_json:
            return {"contacts": []}

        try:
            contacts = json.loads(contacts_json) if isinstance(contacts_json, str) else contacts_json
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse contacts for user {user_id}")
            return {"contacts": []}

        # Convert to list format
        contacts_list = [
            {
                "nickname": nickname,
                "real_name": data.get("real_name"),
                "preferred_app": data.get("preferred_app"),
                "phone_numbers": data.get("phone_numbers", {}),
                "primary_phone": data.get("primary_phone"),
                "emails": data.get("emails", {}),
                "primary_email": data.get("primary_email"),
                "addresses": data.get("addresses", {}),
                "primary_address": data.get("primary_address")
            }
            for nickname, data in contacts.items()
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
        # Normalize the search name
        normalized_name = normalize_nickname(name)

        logger.info(f"Removing contact '{normalized_name}' for user {user_id}")

        # Get existing contacts from preferences
        contacts_json = get_preference(user_id, 'contacts')
        if not contacts_json:
            return {
                "success": False,
                "error": f"Contact '{normalized_name}' not found"
            }

        try:
            contacts = json.loads(contacts_json) if isinstance(contacts_json, str) else contacts_json
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse contacts for user {user_id}")
            return {
                "success": False,
                "error": "Failed to parse existing contacts"
            }

        # First, try to match by nickname (key)
        nickname_to_remove = None
        if normalized_name in contacts:
            nickname_to_remove = normalized_name
        else:
            # If not found by nickname, try to match by real_name
            for nickname, contact in contacts.items():
                real_name = contact.get("real_name", "")
                if real_name and normalize_nickname(real_name) == normalized_name:
                    nickname_to_remove = nickname
                    break

        if nickname_to_remove is None:
            return {
                "success": False,
                "error": f"Contact '{normalized_name}' not found"
            }

        # Remove the contact
        removed_contact = contacts[nickname_to_remove]
        del contacts[nickname_to_remove]

        # Save back to preferences
        success = set_preference(user_id, 'contacts', json.dumps(contacts))

        if success:
            logger.info(f"Successfully removed contact '{nickname_to_remove}' for user {user_id}")
            return {
                "success": True,
                "message": f"Removed contact '{nickname_to_remove}' ({removed_contact.get('real_name')})"
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
        "description": "Look up a contact by name to get their real name, preferred messaging app, phone numbers, email addresses, and postal addresses. Use this BEFORE sending messages. Do NOT use this for searching addresses for agent_get_google_maps_directions unless the query involves someone's name. This automatically searches saved contacts first, then falls back to the device's native phone contacts if not found. If the result includes 'device_contacts', those are matches from the phone — you can use that info directly or suggest saving it. If the contact isn't found anywhere and you have the info you need (e.g. name and messaging app), send the message anyway. Also use this to look up a contact's email for Asana task assignment. The name can match either the nickname or the real name.",
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
        "description": "Delete a saved contact by name. The name can be a nickname or real name. If the name matches multiple contacts, returns an error with the matched contacts so the user can specify which one to remove.",
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
    }
]
