"""Chat-id fields for outbound payloads built where only the integer conversation id is at hand
(device tool modules' `tool_execution` messages, `delete_message` broadcasts).

`current_client_chat_id` is set by the tool runner in app.py to the requesting message's
client_chat_id, so tool modules can emit it without threading it through every tool signature.
"""
import contextvars
from typing import Optional

current_client_chat_id: contextvars.ContextVar = contextvars.ContextVar("current_client_chat_id", default=None)


def _legacy_int(client_chat_id: Optional[str]) -> Optional[int]:
    """LEGACY: remove in Task 8. Integer form of a numeric client_chat_id, else None."""
    if client_chat_id is None:
        return None
    try:
        return int(client_chat_id)
    except (ValueError, TypeError):
        return None


def chat_ids_for_conversation(conversation_id: Optional[int], client_chat_id: Optional[str] = None) -> dict:
    """{"client_chat_id", "client_conversation_id"} for a payload carrying `conversation_id`.

    Uses `client_chat_id` when given; otherwise a legacy negative id is its own client_chat_id
    (the chat's id before its row exists) and a positive id is looked up on its row.
    """
    if client_chat_id is None and isinstance(conversation_id, int) and not isinstance(conversation_id, bool):
        if conversation_id < 0:
            client_chat_id = str(conversation_id)  # LEGACY: remove in Task 8
        elif conversation_id > 0:
            from database import client_chat_id_for  # deferred: keeps tool modules importable without the DB client
            client_chat_id = client_chat_id_for(conversation_id)
    return {
        "client_chat_id": client_chat_id,
        "client_conversation_id": _legacy_int(client_chat_id),  # LEGACY: remove in Task 8
    }


def tool_payload_chat_ids(conversation_id: Optional[int]) -> dict:
    """chat_ids_for_conversation, preferring the running tool's client_chat_id (set by app.py)."""
    return chat_ids_for_conversation(conversation_id, current_client_chat_id.get(None))
