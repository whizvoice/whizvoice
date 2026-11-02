"""
Database operations for message and conversation management.
Extracted from app.py to improve maintainability.
"""
import json
import logging
import time
from typing import Optional, List, Dict, Tuple, Union
from datetime import datetime, timedelta, timezone

from postgrest.exceptions import APIError

from supabase_client import supabase
from redis_managers import MissingTimestampError

logger = logging.getLogger(__name__)


def load_conversation_history(user_id: str, conversation_id: Optional[int] = None) -> List[Dict]:
    """Load conversation history from database and convert to Claude message format"""
    try:
        # FIXED: If no conversation_id specified, return empty history (for new chats)
        # This prevents new chats from loading old conversation history
        if conversation_id is None:
            logger.info(f"New chat session for user {user_id} - returning empty history")
            return []
        else:
            # Handle client-minted references (negative legacy ints / client_chat_id strings)
            actual_conversation_id = conversation_id
            if isinstance(conversation_id, str) or conversation_id < 0:
                actual_conversation_id = resolve_conversation_id(conversation_id, user_id)
                if actual_conversation_id is None:
                    logger.info(f"No existing conversation found for client ref {conversation_id}, treating as new chat")
                    return []  # Treat as new chat if the client ref is not found
                logger.info(f"Found real conversation ID {actual_conversation_id} for client ref {conversation_id}")

            # Verify user owns the specified conversation
            conv_result = supabase.table("conversations").select("id").eq("id", actual_conversation_id).eq("user_id", user_id).execute()
            if not conv_result.data:
                logger.warning(f"Conversation {actual_conversation_id} not found or not owned by user {user_id}")
                return []

        # Get messages for the conversation
        result = supabase.table("messages").select("*").eq("conversation_id", actual_conversation_id).order("timestamp", desc=False).execute()

        # Convert database messages to Claude format
        # Group consecutive messages by role with special handling for tool boundaries
        # This is critical because:
        # 1. Claude API requires strict user/assistant alternation
        # 2. ASSISTANT messages with tool_use must be followed by USER tool_results
        # 3. USER messages should only be merged if they share the same request_id
        claude_messages = []
        current_group = None  # (role, request_id, content_blocks)

        for row in result.data:
            # Skip cancelled messages
            if row.get("cancelled"):
                continue

            message_role = "user" if row["message_sender"] == "USER" else "assistant"
            row_request_id = row.get("request_id")

            # Check if current row contains a tool_use block
            has_tool_use = False
            if row.get("tool_content"):
                for block in row["tool_content"]:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        has_tool_use = True
                        break

            # Determine if this row belongs to current group
            if current_group is not None and message_role == current_group[0]:
                # Same role - but should we merge?
                if message_role == "assistant":
                    # ASSISTANT: Can merge UNLESS the current group already has a tool_use
                    # Once we have a tool_use, we must close the group (need USER tool_result next)
                    # But we CAN add text-only messages before hitting the tool_use
                    should_group = not current_group[3]  # Don't merge if group already has tool_use
                elif message_role == "user":
                    # USER: Always merge consecutive user messages (Claude requires alternation)
                    # tool_result blocks will be ordered before text blocks when building content
                    should_group = True
                else:
                    should_group = False
            else:
                should_group = False

            if should_group:
                # Add to current group
                # Handle messages that may have BOTH text content and tool_content
                # Text must come before tool_use blocks
                if row.get("content") and row["content"].strip():
                    current_group[2].append({"type": "text", "text": row["content"]})
                if row.get("tool_content"):
                    current_group[2].extend(row["tool_content"])
                # Update has_tool_use flag for the group
                if has_tool_use:
                    current_group[3] = True
            else:
                # Flush current group if exists
                if current_group and current_group[2]:
                    claude_messages.append({
                        "role": current_group[0],
                        "content": current_group[2],
                        "_timestamp": current_group[4]
                    })

                # Start new group
                # Handle messages that may have BOTH text content and tool_content
                # Text must come before tool_use blocks
                content_blocks = []
                if row.get("content") and row["content"].strip():
                    content_blocks.append({"type": "text", "text": row["content"]})
                if row.get("tool_content"):
                    content_blocks.extend(row["tool_content"])

                current_group = [message_role, row_request_id, content_blocks, has_tool_use, row.get("timestamp")]

        # Flush final group
        if current_group and current_group[2]:
            claude_messages.append({
                "role": current_group[0],
                "content": current_group[2],
                "_timestamp": current_group[4]
            })

        return claude_messages

    except Exception as e:
        logger.error(f"Error loading conversation history for user {user_id}, conversation {conversation_id}: {str(e)}")
        return []


# Module-level caches for client_chat_id <-> real conversation id.
# Populated on hit only; cleared by clear_id_cache().
_client_chat_id_by_conversation_id: Dict[int, str] = {}
_conversation_id_by_client_chat_id: Dict[Tuple[str, str], int] = {}


def clear_id_cache() -> None:
    """Clear the client_chat_id <-> conversation id caches."""
    _client_chat_id_by_conversation_id.clear()
    _conversation_id_by_client_chat_id.clear()


def invalidate_conversation_cache(conversation_id: int) -> None:
    """Drop a conversation from both id caches (e.g. after soft-delete / stale hit)."""
    client_chat_id = _client_chat_id_by_conversation_id.pop(conversation_id, None)
    for key in [k for k, v in _conversation_id_by_client_chat_id.items()
                if v == conversation_id or (client_chat_id is not None and k[1] == client_chat_id)]:
        _conversation_id_by_client_chat_id.pop(key, None)


def _lookup_by_client_chat_id(client_chat_id: str, user_id: str) -> Optional[int]:
    """Query (uncached) the live conversation id for (user_id, client_chat_id)."""
    result = supabase.table("conversations")\
        .select("id")\
        .eq("client_chat_id", client_chat_id)\
        .eq("user_id", user_id)\
        .is_("deleted_at", "null")\
        .execute()
    if result.data:
        return result.data[0]["id"]
    return None


def client_chat_id_for(conversation_id: int) -> Optional[str]:
    """Return the client_chat_id stored for a real conversation id (cached), or None."""
    cached = _client_chat_id_by_conversation_id.get(conversation_id)
    if cached is not None:
        return cached
    try:
        result = supabase.table("conversations")\
            .select("client_chat_id")\
            .eq("id", conversation_id)\
            .execute()
    except Exception as e:
        logger.error(f"Error looking up client_chat_id for conversation {conversation_id}: {e}")
        return None
    if result.data and result.data[0].get("client_chat_id"):
        client_chat_id = result.data[0]["client_chat_id"]
        _client_chat_id_by_conversation_id[conversation_id] = client_chat_id
        return client_chat_id
    return None


def conversation_id_for(client_chat_id: str, user_id: str) -> Optional[int]:
    """Return the real conversation id for (user_id, client_chat_id) (cached), or None."""
    key = (user_id, client_chat_id)
    cached = _conversation_id_by_client_chat_id.get(key)
    if cached is not None:
        return cached
    try:
        conversation_id = _lookup_by_client_chat_id(client_chat_id, user_id)
    except Exception as e:
        logger.error(f"Error looking up conversation for client_chat_id {client_chat_id}: {e}")
        return None
    if conversation_id is not None:
        _conversation_id_by_client_chat_id[key] = conversation_id
        _client_chat_id_by_conversation_id[conversation_id] = client_chat_id
    return conversation_id


def resolve_conversation_id(ref: Union[int, str, None], user_id: str) -> Optional[int]:
    """
    Resolve a conversation reference to the real DB id.

    - int or numeric string > 0: verify the conversation exists and is owned by the user.
    - negative int (legacy optimistic id): looked up as str(ref) in `client_chat_id`.
    - any other string (UUID client_chat_id, incl. negative numeric strings): looked up
      in `client_chat_id`.

    Soft-deleted rows (`deleted_at IS NOT NULL`) are excluded.
    Returns the real DB ID, or None if not found / not owned / input was None.
    """
    if ref is None:
        return None
    try:
        if isinstance(ref, str):
            stripped = ref.strip()
            if stripped.isdigit() and int(stripped) > 0:
                ref = int(stripped)
            else:
                return _lookup_by_client_chat_id(ref, user_id)
        if ref < 0:
            return _lookup_by_client_chat_id(str(ref), user_id)

        # Positive ID - verify it exists and user owns it
        result = supabase.table("conversations")\
            .select("id")\
            .eq("id", ref)\
            .eq("user_id", user_id)\
            .is_("deleted_at", "null")\
            .execute()

        if result.data:
            return ref
        return None
    except Exception as e:
        logger.error(f"Error resolving conversation ID {ref}: {str(e)}")
        return None


def get_user_message_ids_since_last_bot(conversation_id: int) -> List[int]:
    """Get all user message IDs since the last bot message in a conversation"""
    try:
        # Get all messages ordered by timestamp
        result = supabase.table("messages")\
            .select("id, message_sender")\
            .eq("conversation_id", conversation_id)\
            .is_("cancelled", "null")\
            .order("timestamp", desc=False)\
            .execute()

        if not result.data:
            return []

        # Find user messages since last bot message
        user_message_ids = []
        for msg in reversed(result.data):  # Start from most recent
            if msg["message_sender"] == "ASSISTANT":
                break  # Stop at the most recent bot message
            elif msg["message_sender"] == "USER":
                user_message_ids.append(msg["id"])

        return list(reversed(user_message_ids))  # Return in chronological order
    except Exception as e:
        logger.error(f"Error getting user message IDs for conversation {conversation_id}: {e}")
        return []


def get_non_cancelled_bot_message_ids(conversation_id: int) -> List[int]:
    """Get all non-cancelled bot message IDs in a conversation"""
    try:
        result = supabase.table("messages")\
            .select("id")\
            .eq("conversation_id", conversation_id)\
            .eq("message_sender", "ASSISTANT")\
            .is_("cancelled", "null")\
            .order("id", desc=False)\
            .execute()

        if not result.data:
            return []

        return [msg["id"] for msg in result.data]
    except Exception as e:
        logger.error(f"Error getting bot message IDs for conversation {conversation_id}: {e}")
        return []


def _bump_timestamp_1ms(raw_timestamp: str) -> str:
    """Return an ISO-8601 timestamp exactly 1ms after the given one.

    Supabase returns timestamps with varying microsecond precision (4-6 digits) and a 'Z'
    or +00:00 suffix; Python's fromisoformat needs exactly 6 fractional digits. Normalize
    then add 1ms so the result sorts strictly after the input.
    """
    timestamp_str = raw_timestamp.replace('Z', '+00:00')
    # Normalize fractional seconds to exactly 6 digits (fromisoformat requirement).
    if '.' in timestamp_str:
        parts = timestamp_str.split('.')
        if len(parts) == 2:
            if '+' in parts[1]:
                frac, tz = parts[1].split('+')
                frac = frac.ljust(6, '0')[:6]
                timestamp_str = f"{parts[0]}.{frac}+{tz}"
            elif '-' in parts[1]:
                frac, tz = parts[1].split('-')
                frac = frac.ljust(6, '0')[:6]
                timestamp_str = f"{parts[0]}.{frac}-{tz}"
    bumped = datetime.fromisoformat(timestamp_str) + timedelta(milliseconds=1)
    return bumped.isoformat().replace('+00:00', 'Z')


def save_message_to_db(user_id: str, conversation_id: Optional[int], content: str, message_sender: str, request_id: Optional[str] = None, client_chat_id: Optional[str] = None, client_timestamp: Optional[str] = None, content_type: str = "text", tool_content: Optional[dict] = None, mark_cancelled: bool = False, local_objects=None, client_conversation_id: Optional[int] = None) -> Optional[Tuple[int, int, List[int], str]]:
    """Save a message to the database and return (conversation_id, message_id, cancelled_message_ids, timestamp)

    Args:
        user_id: User ID
        conversation_id: Conversation ID (can be negative for optimistic IDs)
        content: Text content of the message
        message_sender: 'USER' or 'ASSISTANT' (renamed from message_type)
        request_id: Optional request ID for tracking
        client_chat_id: Optional client-minted chat id (stored in conversations.client_chat_id)
        client_timestamp: Optional timestamp from client
        content_type: Type of content - 'text', 'tool_use', 'tool_result', 'mixed', or 'hidden_text'
                      ('hidden_text' is for system-generated user messages that should not appear in the chat UI)
        tool_content: Optional JSONB content for tool-related messages
        mark_cancelled: If True, mark the message as cancelled (for error messages from cancelled requests)
        local_objects: Unused (kept for caller compatibility); ID mappings are cached in this module
        client_conversation_id: Deprecated alias for client_chat_id (str() of it is used); removed in a later task

    Returns:
        Tuple of (conversation_id, message_id, cancelled_message_ids) where cancelled_message_ids
        is a list of message IDs that were marked for cancellation (caller should broadcast deletions)
    """
    try:
        if client_chat_id is None and client_conversation_id is not None:
            client_chat_id = str(client_conversation_id)

        logger.info(f"save_message_to_db called: user_id={user_id}, conversation_id={conversation_id}, message_sender={message_sender}, content_type={content_type}, client_chat_id={client_chat_id}, content='{content[:50] if content else '(empty)'}...'")

        # A legacy negative conversation_id is a client ref: treat it as the client_chat_id
        # (its str() form) when none was given, then resolve to the real id if it exists.
        if conversation_id is not None and conversation_id < 0:
            if client_chat_id is None:
                client_chat_id = str(conversation_id)
            conversation_id = None

        # If no conversation_id provided, look up an existing conversation by client_chat_id
        validated = False
        if conversation_id is None and client_chat_id is not None:
            conversation_id = conversation_id_for(client_chat_id, user_id)
            if conversation_id is not None:
                # The id may come from the cache; confirm the row still exists and is not
                # soft-deleted, otherwise drop the stale entry and create a new conversation.
                stale_check = supabase.table("conversations")\
                    .select("id")\
                    .eq("id", conversation_id)\
                    .eq("user_id", user_id)\
                    .is_("deleted_at", "null")\
                    .execute()
                if stale_check.data:
                    validated = True
                    logger.info(f"Found existing conversation {conversation_id} for client_chat_id {client_chat_id}")
                else:
                    logger.info(f"Cached conversation {conversation_id} for client_chat_id {client_chat_id} is deleted/missing, will create new one")
                    invalidate_conversation_cache(conversation_id)
                    conversation_id = None
            else:
                logger.info(f"No existing conversation found for client_chat_id {client_chat_id}, will create new one")

        # If still no conversation_id, create a new conversation
        if conversation_id is None:
            logger.warning(f"Creating NEW conversation for user {user_id} because conversation_id is None")
            # Create a new conversation
            conversation_data = {
                "user_id": user_id,
                "title": content[:50] + "..." if len(content) > 50 else content,  # Use first part of message as title
                "source": "app"
            }

            if client_chat_id is not None:
                conversation_data["client_chat_id"] = client_chat_id
                logger.info(f"Storing client_chat_id {client_chat_id} for new conversation")

            try:
                conv_result = supabase.table("conversations").insert(conversation_data).execute()
            except APIError as e:
                if client_chat_id is not None and getattr(e, "code", None) == "23505":
                    # Another request created this (user_id, client_chat_id) first: use that row.
                    logger.info(f"Unique violation creating conversation for client_chat_id {client_chat_id}, re-reading existing row")
                    conversation_id = conversation_id_for(client_chat_id, user_id)
                    if conversation_id is None:
                        logger.error(f"Unique violation but no row found for client_chat_id {client_chat_id}")
                        return None
                    conv_result = None
                else:
                    raise

            if conv_result is not None:
                if not conv_result.data:
                    logger.error(f"Failed to create new conversation for user {user_id}")
                    return None

                conversation_id = conv_result.data[0]["id"]
                created_at = conv_result.data[0]["created_at"]
                updated_at = conv_result.data[0]["updated_at"]
                logger.warning(f"Created NEW conversation {conversation_id} for user {user_id} at {created_at} (updated_at: {updated_at})")
        elif not validated:
            # Verify the conversation exists and is not soft-deleted
            logger.debug(f"Validating conversation {conversation_id} for user {user_id}")
            conv_check = supabase.table("conversations")\
                .select("id")\
                .eq("id", conversation_id)\
                .eq("user_id", user_id)\
                .is_("deleted_at", "null")\
                .execute()

            if not conv_check.data:
                logger.error(f"Conversation {conversation_id} not found or is soft-deleted for user {user_id}")
                return None

            logger.debug(f"Using existing conversation {conversation_id} for user {user_id}")

        # Save the message
        logger.info(f"Attempting to save {message_sender} message to conversation_id={conversation_id}, request_id={request_id}, content_type={content_type}")

        # Dedupe USER text messages by (conversation_id, request_id). A client that retries
        # a successfully-sent text message (e.g. orphan-retry after WS disconnect) hits us
        # with the same request_id; without this, supabase gets a second row that never gets
        # cleaned up. Each fresh user input gets a new UUID, so this only collides on retries.
        # Scoped to content_type="text" so server-side PENDING tool_result writes (which
        # share the user's request_id and message_sender=USER) are not swallowed.
        if message_sender == "USER" and content_type == "text" and request_id and conversation_id and conversation_id > 0:
            try:
                existing = supabase.table("messages") \
                    .select("id, timestamp") \
                    .eq("conversation_id", conversation_id) \
                    .eq("request_id", request_id) \
                    .eq("message_sender", "USER") \
                    .eq("content_type", "text") \
                    .limit(1).execute()
                if existing.data:
                    existing_id = existing.data[0]["id"]
                    existing_timestamp = existing.data[0]["timestamp"]
                    logger.info(f"USER message dedupe: returning existing message_id={existing_id} for conversation_id={conversation_id}, request_id={request_id}")
                    # Canonical order is (conversation_id, message_id, cancelled_ids, timestamp).
                    # This early-return previously returned (existing_id, conversation_id, []) — which
                    # both omitted the timestamp (breaking the 4-tuple contract callers now unpack) and
                    # swapped conversation_id/message_id relative to the main return.
                    return (conversation_id, existing_id, [], existing_timestamp)
            except Exception as e:
                logger.error(f"Error checking for existing USER message dedupe (conversation_id={conversation_id}, request_id={request_id}): {e}")
                # Fall through to insert — losing dedupe is preferable to losing a message

        # For ASSISTANT messages with request_id, collect any previous ASSISTANT messages with the same request_id to cancel
        # This handles the case where streaming responses create multiple intermediate messages
        # IMPORTANT: We never cancel tool_use or tool_result messages, as they represent actual tool executions
        # NOTE: We collect IDs here but don't cancel yet - caller will use cancel_and_broadcast_messages() helper
        cancelled_message_ids = []
        if message_sender == "ASSISTANT" and request_id:
            try:
                # Find all previous ASSISTANT messages with this request_id that aren't already cancelled
                # Exclude tool_use and tool_result messages from cancellation
                previous_messages = supabase.table("messages")\
                    .select("id")\
                    .eq("conversation_id", conversation_id)\
                    .eq("request_id", request_id)\
                    .eq("message_sender", "ASSISTANT")\
                    .is_("cancelled", "null")\
                    .not_.in_("content_type", ["tool_use", "tool_result"])\
                    .execute()

                if previous_messages.data:
                    messages_to_cancel = [msg["id"] for msg in previous_messages.data]

                    if messages_to_cancel:
                        cancelled_message_ids = messages_to_cancel
                        logger.info(f"Found {len(cancelled_message_ids)} previous ASSISTANT message(s) to cancel for request_id={request_id}: {cancelled_message_ids}")
                        logger.info(f"Note: Caller should use cancel_and_broadcast_messages() to actually cancel and broadcast")
                    else:
                        logger.info(f"No messages to cancel for request_id={request_id} (only tool_use/tool_result messages found)")
            except Exception as e:
                logger.error(f"Error finding previous ASSISTANT messages to cancel for request_id={request_id}: {e}")

        # Prepare message data
        message_data = {
            "conversation_id": conversation_id,
            "content": content,
            "message_sender": message_sender,
            "content_type": content_type,
            "request_id": request_id
        }

        # Mark as cancelled if requested (for error messages from cancelled requests)
        if mark_cancelled:
            message_data["cancelled"] = "now()"
            logger.info(f"Marking message as cancelled for request_id={request_id}")

        # Add tool_content if provided
        if tool_content is not None:
            message_data["tool_content"] = tool_content
            logger.info(f"Including tool_content in message: {json.dumps(tool_content)[:100]}...")

        # For USER messages with client_timestamp, use the provided timestamp to preserve message order
        if message_sender == "USER" and client_timestamp:
            # Client timestamp is already in ISO format from Android client
            message_data["timestamp"] = client_timestamp
            logger.info(f"Using client-provided timestamp for USER message: {client_timestamp}")

        # Server-generated hidden_text USER fillers (e.g. the concurrent-request-lap guard in app.py)
        # have no client_timestamp. Anchor them 1ms after the latest message in the conversation so they
        # sort STRICTLY after the trailing assistant turn they are meant to follow. A plain now() could
        # sort before that assistant message under client/server clock skew (assistant timestamps are
        # derived from client timestamps), which would leave the context still ending on an assistant
        # message and re-trigger the guard on every reload.
        elif message_sender == "USER" and content_type == "hidden_text" and not client_timestamp and conversation_id:
            latest_result = supabase.table("messages")\
                .select("timestamp")\
                .eq("conversation_id", conversation_id)\
                .order("timestamp", desc=True)\
                .limit(1)\
                .execute()
            if latest_result.data:
                message_data["timestamp"] = _bump_timestamp_1ms(latest_result.data[0]["timestamp"])
                logger.info(f"Setting hidden_text USER filler timestamp to {message_data['timestamp']} (1ms after latest message in conversation {conversation_id})")
            else:
                # No prior messages to anchor against (not expected for a filler) — use current time.
                message_data["timestamp"] = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
                logger.info(f"Setting hidden_text USER filler timestamp to now() {message_data['timestamp']} (no prior messages in conversation {conversation_id})")

        # For ASSISTANT messages with request_id, set timestamp to be right after all other messages in this request
        # This ensures proper message ordering: text_before -> tool_use -> tool_result -> text_after
        if message_sender == "ASSISTANT" and request_id:
            # Find ALL messages with this request_id to determine the max timestamp
            all_msgs_result = supabase.table("messages")\
                .select("timestamp")\
                .eq("conversation_id", conversation_id)\
                .eq("request_id", request_id)\
                .execute()

            if all_msgs_result.data:
                # Find the maximum timestamp among all messages in this request, then sort 1ms after it
                # so ordering stays text_before -> tool_use -> tool_result -> text_after.
                max_timestamp = max(msg["timestamp"] for msg in all_msgs_result.data)
                message_data["timestamp"] = _bump_timestamp_1ms(max_timestamp)
                logger.info(f"Setting ASSISTANT message timestamp to {message_data['timestamp']} (1ms after max timestamp {max_timestamp} in request)")
            else:
                raise MissingTimestampError(f"No messages found with request_id {request_id} to calculate ASSISTANT timestamp")

        # Require timestamp - no fallback to DB default
        if "timestamp" not in message_data:
            raise MissingTimestampError(
                f"Required timestamp missing for {message_sender} message in conversation {conversation_id}. "
                f"USER messages require client_timestamp, ASSISTANT messages require request_id with existing messages."
            )

        logger.debug(f"Inserting message with timestamp: {message_data['timestamp']}")
        result = supabase.table("messages").insert(message_data).execute()

        if not result.data:
            logger.error(f"Failed to save {message_sender} message to conversation {conversation_id} - no data returned from insert")
            return None

        # Extract the saved message ID
        saved_message = result.data[0]
        message_id = saved_message.get("id")

        actual_timestamp = saved_message.get("timestamp")
        logger.debug(f"Message {message_id} saved with timestamp: {actual_timestamp}")
        saved_conv_id = saved_message.get("conversation_id")
        logger.info(f"Successfully saved {message_sender} message: message_id={message_id}, conversation_id={saved_conv_id}, request_id={request_id}, content_type={content_type}")

        return (conversation_id, message_id, cancelled_message_ids, actual_timestamp)

    except Exception as e:
        logger.error(f"Error saving message to database: {str(e)}")
        return None


def save_messages_to_db(messages: List[Dict], conversation_id: int, request_id: str) -> List[Optional[int]]:
    """Save multiple messages atomically to the database using batch insert.

    This is used for atomic insertion of related messages (e.g., tool_use + pending tool_result)
    to prevent race conditions where another worker might read partial state.

    Args:
        messages: List of message dicts, each containing:
            - content: Text content of the message
            - message_sender: 'USER' or 'ASSISTANT'
            - content_type: 'text', 'tool_use', 'tool_result', or 'mixed'
            - tool_content: Optional JSONB content for tool-related messages
            - timestamp: ISO timestamp string for message ordering
        conversation_id: The conversation ID (must be resolved, not optimistic)
        request_id: Request ID for tracking

    Returns:
        List of message IDs for each inserted message, or empty list on failure
    """
    try:
        if not messages:
            logger.warning("save_messages_to_db called with empty messages list")
            return []

        logger.info(f"save_messages_to_db called: conversation_id={conversation_id}, request_id={request_id}, message_count={len(messages)}")

        # Prepare all message data with conversation_id and request_id
        message_data_list = []
        for i, msg in enumerate(messages):
            message_data = {
                "conversation_id": conversation_id,
                "content": msg.get("content", ""),
                "message_sender": msg["message_sender"],
                "content_type": msg.get("content_type", "text"),
                "request_id": request_id
            }

            # Add tool_content if provided
            if msg.get("tool_content") is not None:
                message_data["tool_content"] = msg["tool_content"]

            # Add timestamp if provided
            if msg.get("timestamp"):
                message_data["timestamp"] = msg["timestamp"]

            message_data_list.append(message_data)
            logger.info(f"  Message {i}: sender={msg['message_sender']}, type={msg.get('content_type', 'text')}, timestamp={msg.get('timestamp', 'default')}")

        # Batch insert all messages atomically
        result = supabase.table("messages").insert(message_data_list).execute()

        if not result.data:
            logger.error(f"Failed to batch save {len(messages)} messages to conversation {conversation_id} - no data returned")
            return []

        # Extract saved message IDs
        message_ids = [msg.get("id") for msg in result.data]
        logger.info(f"Successfully batch saved {len(message_ids)} messages to conversation {conversation_id}: {message_ids}")

        return message_ids

    except Exception as e:
        logger.error(f"Error batch saving messages to database: {str(e)}")
        return []


def update_tool_result_in_db(conversation_id: int, tool_use_id: str, result_content: dict, user_id: int = None) -> bool:
    """Update a pending tool_result message with the actual result

    Args:
        conversation_id: The conversation ID containing the tool result message
        tool_use_id: The tool_use_id to identify which tool_result to update
        result_content: The actual tool execution result to replace the pending content
        user_id: The user ID (required if conversation_id is optimistic/negative)

    Returns:
        True if update successful, False otherwise
    """
    try:
        # Handle optimistic conversation IDs (negative IDs)
        if conversation_id is not None and (isinstance(conversation_id, str) or conversation_id < 0):
            if user_id is None:
                logger.error(f"user_id required to resolve client conversation ref {conversation_id}")
                return False

            real_id = resolve_conversation_id(conversation_id, user_id)
            if real_id is None:
                logger.error(f"No conversation found for client ref {conversation_id}")
                return False
            logger.info(f"Found real conversation ID {real_id} for client ref {conversation_id}")
            conversation_id = real_id

        logger.info(f"Updating tool_result for tool_use_id={tool_use_id} in conversation={conversation_id}")

        # Find the pending tool_result message
        # Retry up to 3 times with 1s delay - handles race condition where
        # tool executes before placeholder is saved to DB
        max_retries = 3
        result = None
        for attempt in range(max_retries):
            result = supabase.table("messages")\
                .select("id, tool_content, cancelled")\
                .eq("conversation_id", conversation_id)\
                .eq("content_type", "tool_result")\
                .execute()

            if result.data:
                break
            if attempt < max_retries - 1:
                logger.info(f"No tool_result messages found yet for conversation {conversation_id}, retrying in 1s (attempt {attempt + 1}/{max_retries})")
                time.sleep(1)

        if not result.data:
            logger.error(f"No tool_result messages found in conversation {conversation_id} after {max_retries} attempts")
            return False

        # Find the message with matching tool_use_id
        message_to_update = None
        for msg in result.data:
            tool_content = msg.get("tool_content", [])
            if isinstance(tool_content, list):
                for block in tool_content:
                    if isinstance(block, dict) and block.get("tool_use_id") == tool_use_id:
                        message_to_update = msg
                        break
            if message_to_update:
                break

        if not message_to_update:
            logger.error(f"No tool_result message found with tool_use_id={tool_use_id}")
            return False

        message_id = message_to_update["id"]
        existing_tool_content = message_to_update.get("tool_content", [])

        # Update only the SPECIFIC tool_result block, keep others intact
        # This is important when multiple tool_results are in the same message
        updated_tool_content = []
        for block in existing_tool_content:
            if isinstance(block, dict) and block.get("tool_use_id") == tool_use_id:
                # Replace this specific block with actual result
                updated_tool_content.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": json.dumps(result_content)
                })
            else:
                # Keep other blocks unchanged
                updated_tool_content.append(block)

        # Update the message in the database
        update_result = supabase.table("messages")\
            .update({"tool_content": updated_tool_content})\
            .eq("id", message_id)\
            .execute()

        if update_result.data:
            logger.info(f"Successfully updated tool_result message {message_id} for tool_use_id={tool_use_id}")
            return True
        else:
            logger.error(f"Failed to update tool_result message {message_id}")
            return False

    except Exception as e:
        logger.error(f"Error updating tool_result in database: {str(e)}")
        return False
