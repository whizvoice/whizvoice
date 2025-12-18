"""
Database operations for message and conversation management.
Extracted from app.py to improve maintainability.
"""
import json
import logging
from typing import Optional, List, Dict, Tuple
from datetime import datetime, timedelta

from supabase_client import supabase

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
            # Handle optimistic/negative conversation IDs
            actual_conversation_id = conversation_id
            if conversation_id < 0:
                logger.info(f"Received optimistic conversation ID {conversation_id}, looking up real ID")
                # Look up the real conversation using the optimistic_chat_id
                opt_result = supabase.table("conversations")\
                    .select("id")\
                    .eq("user_id", user_id)\
                    .eq("optimistic_chat_id", str(conversation_id))\
                    .is_("deleted_at", "null")\
                    .execute()

                if opt_result.data and len(opt_result.data) > 0:
                    actual_conversation_id = opt_result.data[0]["id"]
                    logger.info(f"Found real conversation ID {actual_conversation_id} for optimistic ID {conversation_id}")
                else:
                    logger.info(f"No existing conversation found for optimistic ID {conversation_id}, treating as new chat")
                    return []  # Treat as new chat if optimistic ID not found

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
                    # USER: Only merge if same request_id
                    should_group = (row_request_id == current_group[1])
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


def save_message_to_db(user_id: str, conversation_id: Optional[int], content: str, message_sender: str, request_id: Optional[str] = None, client_conversation_id: Optional[int] = None, client_timestamp: Optional[str] = None, content_type: str = "text", tool_content: Optional[dict] = None, mark_cancelled: bool = False) -> Optional[Tuple[int, int, List[int]]]:
    """Save a message to the database and return (conversation_id, message_id, cancelled_message_ids)

    Args:
        user_id: User ID
        conversation_id: Conversation ID (can be negative for optimistic IDs)
        content: Text content of the message
        message_sender: 'USER' or 'ASSISTANT' (renamed from message_type)
        request_id: Optional request ID for tracking
        client_conversation_id: Optional optimistic conversation ID from client
        client_timestamp: Optional timestamp from client
        content_type: Type of content - 'text', 'tool_use', 'tool_result', or 'mixed'
        tool_content: Optional JSONB content for tool-related messages
        mark_cancelled: If True, mark the message as cancelled (for error messages from cancelled requests)

    Returns:
        Tuple of (conversation_id, message_id, cancelled_message_ids) where cancelled_message_ids
        is a list of message IDs that were marked for cancellation (caller should broadcast deletions)
    """
    try:
        logger.info(f"save_message_to_db called: user_id={user_id}, conversation_id={conversation_id}, message_sender={message_sender}, content_type={content_type}, client_conversation_id={client_conversation_id}, content='{content[:50] if content else '(empty)'}...'")

        # Handle optimistic conversation IDs (negative IDs)
        original_optimistic_id = None
        if conversation_id is not None and conversation_id < 0:
            logger.info(f"Received optimistic conversation ID {conversation_id}, looking up real ID")
            original_optimistic_id = conversation_id
            # Look up the real conversation using the optimistic_chat_id
            conv_result = supabase.table("conversations")\
                .select("id")\
                .eq("optimistic_chat_id", str(conversation_id))\
                .eq("user_id", user_id)\
                .is_("deleted_at", "null")\
                .execute()

            if conv_result.data:
                real_id = conv_result.data[0]["id"]
                logger.info(f"Found real conversation ID {real_id} for optimistic ID {conversation_id}")
                conversation_id = real_id
            else:
                logger.info(f"No existing conversation found for optimistic ID {conversation_id}, will create new one")
                conversation_id = None

        # Validate client_conversation_id - it should ONLY be negative (optimistic) values
        # Convert to int if it's a string number for validation
        if client_conversation_id is not None:
            try:
                client_conv_id_int = int(client_conversation_id) if isinstance(client_conversation_id, str) else client_conversation_id
                if client_conv_id_int > 0:
                    error_msg = f"Invalid client_conversation_id: {client_conversation_id}. Client conversation IDs must be negative (optimistic) values. The client should use the conversation_id parameter for server-assigned IDs."
                    logger.error(error_msg)
                    return {"error": error_msg, "status": 400}
                # Use the integer version for all subsequent operations
                client_conversation_id = client_conv_id_int
            except (ValueError, TypeError):
                logger.warning(f"client_conversation_id is not a valid number in save_message_to_db: {client_conversation_id} (type: {type(client_conversation_id)})")

        # If no conversation_id provided, check if we can find one by optimistic client_conversation_id
        if conversation_id is None and client_conversation_id is not None:
            # client_conversation_id should always be negative (optimistic) at this point
            logger.info(f"No conversation_id but have optimistic client_conversation_id {client_conversation_id}, checking for existing conversation")
            # Look up existing conversation by optimistic_chat_id
            conv_result = supabase.table("conversations")\
                .select("id")\
                .eq("optimistic_chat_id", str(client_conversation_id))\
                .eq("user_id", user_id)\
                .is_("deleted_at", "null")\
                .execute()

            if conv_result.data:
                conversation_id = conv_result.data[0]["id"]
                logger.info(f"Found existing conversation {conversation_id} for optimistic_chat_id {client_conversation_id}")
                # Don't return here - we still need to save the message below
            else:
                logger.info(f"No existing conversation found for optimistic_chat_id {client_conversation_id}, will create new one")

        # If still no conversation_id, create a new conversation
        if conversation_id is None:
            logger.warning(f"Creating NEW conversation for user {user_id} because conversation_id is None")
            # Create a new conversation
            conversation_data = {
                "user_id": user_id,
                "title": content[:50] + "..." if len(content) > 50 else content,  # Use first part of message as title
                "source": "app"
            }

            # If this is an optimistic chat (negative ID), store it
            # Priority: use original_optimistic_id if we had one, otherwise check client_conversation_id
            optimistic_id_to_store = original_optimistic_id or client_conversation_id
            if optimistic_id_to_store is not None and optimistic_id_to_store < 0:
                conversation_data["optimistic_chat_id"] = str(optimistic_id_to_store)
                logger.info(f"Storing optimistic_chat_id {optimistic_id_to_store} for new conversation")

            conv_result = supabase.table("conversations").insert(conversation_data).execute()

            if not conv_result.data:
                logger.error(f"Failed to create new conversation for user {user_id}")
                return None

            conversation_id = conv_result.data[0]["id"]
            created_at = conv_result.data[0]["created_at"]
            updated_at = conv_result.data[0]["updated_at"]
            logger.warning(f"Created NEW conversation {conversation_id} for user {user_id} at {created_at} (updated_at: {updated_at})")
        else:
            # Verify the conversation exists and is not soft-deleted
            logger.info(f"Validating conversation {conversation_id} for user {user_id}")
            conv_check = supabase.table("conversations")\
                .select("id")\
                .eq("id", conversation_id)\
                .eq("user_id", user_id)\
                .is_("deleted_at", "null")\
                .execute()

            if not conv_check.data:
                logger.error(f"Conversation {conversation_id} not found or is soft-deleted for user {user_id}")
                return None

            logger.info(f"Using existing conversation {conversation_id} for user {user_id}")

        # Save the message
        logger.info(f"Attempting to save {message_sender} message to conversation_id={conversation_id}, request_id={request_id}, content_type={content_type}")

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
                    .select("id, content_type")\
                    .eq("conversation_id", conversation_id)\
                    .eq("request_id", request_id)\
                    .eq("message_sender", "ASSISTANT")\
                    .is_("cancelled", "null")\
                    .execute()

                if previous_messages.data:
                    # Filter out tool_use and tool_result messages - we never cancel these
                    messages_to_cancel = [
                        msg["id"] for msg in previous_messages.data
                        if msg.get("content_type") not in ["tool_use", "tool_result"]
                    ]

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
                # Find the maximum timestamp among all messages in this request
                max_timestamp = max(msg["timestamp"] for msg in all_msgs_result.data)
                # Parse the timestamp and add 1ms

                # Fix: Normalize timestamp format from Supabase
                # Supabase sometimes returns timestamps with varying microsecond precision (4-6 digits)
                # Python's fromisoformat expects exactly 6 digits for microseconds
                timestamp_str = max_timestamp.replace('Z', '+00:00')

                # Check if timestamp has microseconds and normalize to 6 digits
                if '.' in timestamp_str:
                    # Split into main part and fractional seconds + timezone
                    parts = timestamp_str.split('.')
                    if len(parts) == 2:
                        # Further split fractional part from timezone
                        if '+' in parts[1]:
                            frac, tz = parts[1].split('+')
                            # Pad or truncate fractional seconds to exactly 6 digits
                            frac = frac.ljust(6, '0')[:6]
                            timestamp_str = f"{parts[0]}.{frac}+{tz}"
                        elif '-' in parts[1]:
                            frac, tz = parts[1].split('-')
                            frac = frac.ljust(6, '0')[:6]
                            timestamp_str = f"{parts[0]}.{frac}-{tz}"

                max_dt = datetime.fromisoformat(timestamp_str)
                assistant_dt = max_dt + timedelta(milliseconds=1)
                # Format as ISO string with timezone
                message_data["timestamp"] = assistant_dt.isoformat().replace('+00:00', 'Z')
                logger.info(f"Setting ASSISTANT message timestamp to {message_data['timestamp']} (1ms after max timestamp {max_timestamp} in request)")
            else:
                logger.warning(f"No messages found with request_id {request_id}, using default timestamp")

        # Debug: Log exactly what we're sending to Supabase
        if "timestamp" in message_data:
            logger.info(f"DEBUG: Inserting message with timestamp field: {message_data['timestamp']}")
        else:
            logger.info(f"DEBUG: Inserting message WITHOUT timestamp field (will use DB default)")

        result = supabase.table("messages").insert(message_data).execute()

        if not result.data:
            logger.error(f"Failed to save {message_sender} message to conversation {conversation_id} - no data returned from insert")
            return None

        # Extract the saved message ID
        saved_message = result.data[0]
        message_id = saved_message.get("id")

        # Debug: Log what timestamp was actually saved
        actual_timestamp = saved_message.get("timestamp")
        logger.info(f"DEBUG: Message {message_id} saved with timestamp: {actual_timestamp}")
        saved_conv_id = saved_message.get("conversation_id")
        logger.info(f"Successfully saved {message_sender} message: message_id={message_id}, conversation_id={saved_conv_id}, request_id={request_id}, content_type={content_type}")

        # Update conversation last_message_time and updated_at for incremental sync
        update_result = supabase.table("conversations").update({
            "last_message_time": "now()",
            "updated_at": "now()"  # Critical: update this so incremental sync catches new messages
        }).eq("id", conversation_id).execute()

        if update_result.data:
            logger.info(f"Updated conversation {conversation_id} timestamps for {message_sender} message")
        else:
            logger.warning(f"Failed to update conversation {conversation_id} timestamps")

        return (conversation_id, message_id, cancelled_message_ids)

    except Exception as e:
        logger.error(f"Error saving message to database: {str(e)}")
        return None


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
        if conversation_id is not None and conversation_id < 0:
            if user_id is None:
                logger.error(f"user_id required to resolve optimistic conversation_id {conversation_id}")
                return False

            logger.info(f"Received optimistic conversation ID {conversation_id}, looking up real ID")
            # Look up the real conversation using the optimistic_chat_id
            conv_result = supabase.table("conversations")\
                .select("id")\
                .eq("optimistic_chat_id", str(conversation_id))\
                .eq("user_id", user_id)\
                .is_("deleted_at", "null")\
                .execute()

            if conv_result.data:
                real_id = conv_result.data[0]["id"]
                logger.info(f"Found real conversation ID {real_id} for optimistic ID {conversation_id}")
                conversation_id = real_id
            else:
                logger.error(f"No conversation found for optimistic ID {conversation_id}")
                return False

        logger.info(f"Updating tool_result for tool_use_id={tool_use_id} in conversation={conversation_id}")

        # Find the pending tool_result message
        result = supabase.table("messages")\
            .select("id, tool_content, cancelled")\
            .eq("conversation_id", conversation_id)\
            .eq("content_type", "tool_result")\
            .execute()

        if not result.data:
            logger.error(f"No tool_result messages found in conversation {conversation_id}")
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
