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


def add_contact_preference(user_id: str, nickname: Optional[str], real_name: str, preferred_app: str, email: Optional[str] = None) -> Dict[str, Any]:
    """
    Add or update a contact with nickname, real name, and preferred messaging app.

    Args:
        user_id: The user ID
        nickname: The nickname for the contact (e.g., 'husband', 'mom', 'boss').
                  If not provided, defaults to the real_name.
        real_name: The real name of the contact (e.g., 'Robin Pham')
        preferred_app: The preferred messaging app ('whatsapp' or 'sms')
        email: Optional email address for the contact (used for Asana task assignment)

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

        # Preserve existing emails list when updating
        existing_emails = contacts[normalized_nickname].get("emails", []) if is_update else []

        contacts[normalized_nickname] = {
            "real_name": real_name,
            "preferred_app": preferred_app
        }

        # Handle email: preserve existing list, add new email if provided
        if email:
            if email not in existing_emails:
                existing_emails.append(email)
            contacts[normalized_nickname]["emails"] = existing_emails
        elif existing_emails:
            contacts[normalized_nickname]["emails"] = existing_emails

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


def get_contact_preference(user_id: str, name: str) -> Dict[str, Any]:
    """
    Look up a contact by name (can be nickname or real name) to get their details.

    Args:
        user_id: The user ID
        name: The name to look up - can be a nickname (e.g., 'husband', 'mom') or real name (e.g., 'Robin Pham')

    Returns:
        Dictionary with found status and contact details if found
    """
    try:
        # Normalize the search name
        normalized_name = normalize_nickname(name)

        logger.info(f"Looking up contact '{normalized_name}' for user {user_id}")

        # Get contacts from preferences
        contacts_json = get_preference(user_id, 'contacts')
        if not contacts_json:
            return {"found": False}

        try:
            contacts = json.loads(contacts_json) if isinstance(contacts_json, str) else contacts_json
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse contacts for user {user_id}")
            return {"found": False}

        # First, try to match by nickname (key)
        if normalized_name in contacts:
            contact = contacts[normalized_name]
            return {
                "found": True,
                "nickname": normalized_name,
                "real_name": contact.get("real_name"),
                "preferred_app": contact.get("preferred_app"),
                "emails": contact.get("emails", [])
            }

        # If not found by nickname, try to match by real_name
        for nickname, contact in contacts.items():
            real_name = contact.get("real_name", "")
            if real_name and normalize_nickname(real_name) == normalized_name:
                return {
                    "found": True,
                    "nickname": nickname,
                    "real_name": real_name,
                    "preferred_app": contact.get("preferred_app"),
                    "emails": contact.get("emails", [])
                }

        return {"found": False, "note": "Contact wasn't found. If you have enough information to proceed, do so, otherwise, ask the user to add the contact."}

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
                "emails": data.get("emails", [])
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
        "description": "Add or update a contact. Contact must have a real name, which can be mapped to a nickname and/or preferred messaging app. Use this when the user wants to save how to reach someone (e.g., 'My husband is Robin Pham, he uses WhatsApp'). If no nickname is provided, the real name will be used as the nickname.",
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
                "email": {
                    "type": "string",
                    "description": "The email address for the contact, used for assigning Asana tasks. Added to the contact's email list."
                }
            },
            "required": ["real_name", "preferred_app"]
        }
    },
    {
        "type": "custom",
        "name": "get_contact_preference",
        "description": "Look up a contact by name to get their real name, preferred messaging app, and email addresses. Use this BEFORE sending messages when the user refers to someone by a nickname (e.g., 'my husband', 'mom', 'boss') or by their real name (e.g., 'Robin Pham'). Also use this to look up a contact's email for Asana task assignment. The name can match either the nickname or the real name.",
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
        "description": "List all saved contacts with their nicknames, real names, preferred messaging apps, and email addresses.",
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
