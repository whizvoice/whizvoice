"""String formats for live-conversation keys (sessions, pub/sub channels, counters).

Every key is derived from the client-minted `client_chat_id`, which exists from a
chat's first byte and never changes, so no key is ever renamed or redirected.
Build these keys only through these helpers.
"""


def session_id(user_id: str, client_chat_id: str) -> str:
    """Chat session id (Redis context, timestamps, active requests, socket registration)."""
    return f"ws_{user_id}_conv_{client_chat_id}"


def channel(client_chat_id: str) -> str:
    """Redis pub/sub channel for broadcasts to every socket on a conversation."""
    return f"conversation:{client_chat_id}"


def pending_tools_key(client_chat_id: str) -> str:
    """Redis counter of in-flight tool executions for a conversation."""
    return f"conversation:{client_chat_id}:pending_tools"
