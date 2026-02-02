"""
Session and tool execution cleanup tasks.
"""
import asyncio
import json
import logging
import time
import traceback
from typing import Optional

from redis_helpers import (
    get_chat_messages,
    get_user_sessions,
    remove_user_session,
    remove_session_timestamp,
    clear_session_mappings,
    get_active_requests,
    get_stale_sessions,
    get_session_timestamp,
    clear_chat_session,
    redis_managers
)
from tool_result_handler import tool_result_handler
from screen_agent_queue import screen_agent_queue

logger = logging.getLogger(__name__)

# Constants
SESSION_TIMEOUT_SECONDS = 900  # 15 minutes timeout for inactive sessions
CLEANUP_INTERVAL_SECONDS = 300  # Run cleanup every 5 minutes
MAX_SESSIONS_PER_USER = 5  # Max concurrent sessions per user


async def unsubscribe_from_conversation(session_id: str):
    """Unsubscribe from Redis pub/sub for a conversation

    This will be imported from websocket_manager when that module is extracted.
    For now, we import it from the parent module.
    """
    # This function is defined in app.py and will be imported from there
    # When websocket_manager.py is created, this import should be updated
    pass


async def cleanup_session(session_id: str, user_id: Optional[str] = None, conversation_id: Optional[int] = None):
    """Clean up a session when a WebSocket disconnects"""
    # Don't delete chat history immediately - let any active message processing tasks complete
    # They need the session history to generate responses
    # The session will be cleaned up when the task completes or after a timeout
    logger.info(f"Cleaning up session {session_id}, but keeping chat history for active tasks")

    # Clean up screen agent queue for this session
    await screen_agent_queue.cleanup_session(session_id)
    logger.info(f"Cleaned up screen agent queue: {session_id}")

    # Clean up session timestamp
    await remove_session_timestamp(session_id)
    logger.info(f"Cleaned up session timestamp: {session_id}")

    # Cancel Redis listener task first (before closing pubsub)
    if redis_managers and "local_objects" in redis_managers:
        cancelled = await redis_managers["local_objects"].cancel_listener_task(session_id)
        if cancelled:
            logger.info(f"Cancelled Redis listener task for session {session_id}")

    # Clean up Redis subscriptions (after cancelling the listener)
    # Import from app.py for now
    from app import unsubscribe_from_conversation as app_unsubscribe
    await app_unsubscribe(session_id)

    # Clean up session mappings
    await clear_session_mappings(session_id)
    logger.info(f"Cleaned up session mappings: {session_id}")

    # Clean up conversation websockets (check all conversations, not just the provided one)
    if redis_managers and "local_objects" in redis_managers:
        await redis_managers["local_objects"].unregister_conversation_websocket(session_id)

    # DON'T clean up active requests immediately - we need to track them to know when tasks complete
    # The tasks themselves will remove their entries when they finish
    active_reqs = await get_active_requests(session_id)
    num_requests = len(active_reqs)
    if num_requests > 0:
        logger.info(f"Keeping {num_requests} active requests for session {session_id} - they will clean up when complete")

    if user_id:
        await remove_user_session(user_id, session_id)
        logger.info(f"Removed session {session_id} from user {user_id} sessions")

        # Check if user has no more sessions
        remaining_sessions = await get_user_sessions(user_id)
        if not remaining_sessions:
            logger.info(f"Cleaned up empty user sessions for user {user_id}")


async def evict_user_sessions_if_needed(user_id: str, new_session_id: str) -> None:
    """Evict old sessions if user has reached the session limit

    Eviction priority (highest to lowest):
    1. Dead/disconnected sessions (already cleaned up)
    2. Least recently active session
    3. Never evict the new session being created
    """
    # Get current sessions first
    current_sessions = await get_user_sessions(user_id)
    if not current_sessions:
        return

    # Check for dead sessions (sessions without chat history)
    dead_sessions = []
    for sess in current_sessions:
        if sess != new_session_id:
            messages = await get_chat_messages(sess)
            if not messages:
                dead_sessions.append(sess)

    # Remove dead sessions
    if dead_sessions:
        for dead_sess in dead_sessions:
            logger.info(f"Removing dead session {dead_sess} from Redis during eviction check")
            await remove_user_session(user_id, dead_sess)
        current_sessions = await get_user_sessions(user_id)

    # If at or under the limit, no eviction needed (new session already added)
    if len(current_sessions) <= MAX_SESSIONS_PER_USER:
        return

    logger.info(f"User {user_id} at session limit ({MAX_SESSIONS_PER_USER}), need to evict a session")

    # Find the least recently active session
    current_time = time.time()
    inactive_threshold = current_time - 120  # Sessions inactive for 2 minutes

    # Collect sessions with their activity times
    sessions_with_activity = []

    current_sessions = await get_user_sessions(user_id)

    for sess_id in current_sessions:
        if sess_id == new_session_id:
            continue  # Don't evict the session we're trying to create

        # Skip sessions that are already cleaned up (disconnected)
        messages = await get_chat_messages(sess_id)
        if not messages:
            logger.debug(f"Skipping already-disconnected session {sess_id}")
            continue

        last_activity = await get_session_timestamp(sess_id) or 0

        sessions_with_activity.append((sess_id, last_activity))

    # Sort by activity time (oldest first)
    sessions_with_activity.sort(key=lambda x: x[1])

    # Find eviction candidate
    eviction_candidate = None
    if sessions_with_activity:
        # Evict the least recently active session
        eviction_candidate, oldest_activity = sessions_with_activity[0]
        inactive_duration = int(current_time - oldest_activity)
        logger.info(f"Selected session {eviction_candidate} for eviction (inactive for {inactive_duration}s)")

    if eviction_candidate:
        evicted_timestamp = await get_session_timestamp(eviction_candidate) or 0
        logger.info(f"Evicting session {eviction_candidate} (last active: {int(current_time - evicted_timestamp)} seconds ago)")

        # Extract conversation_id from the evicted session if possible
        evicted_conversation_id = None
        parts = eviction_candidate.split('_')
        if len(parts) >= 4 and parts[2] == 'conv':
            try:
                evicted_conversation_id = int(parts[3])
            except ValueError:
                pass

        # Find and notify the websocket for the evicted session
        ws_to_notify = None
        if redis_managers and "local_objects" in redis_managers:
            if evicted_conversation_id:
                connections = await redis_managers["local_objects"].get_conversation_websockets(evicted_conversation_id)
                for sid, ws in connections:
                    if sid == eviction_candidate:
                        ws_to_notify = ws
                        break

        if ws_to_notify:
            try:
                eviction_message = {
                    "type": "session_evicted",
                    "code": "MAX_SESSIONS_REACHED",
                    "reason": "New connection from another device"
                }
                await ws_to_notify.send_text(json.dumps(eviction_message))
                await ws_to_notify.close(code=1000, reason="Session evicted: max sessions reached")
            except Exception as e:
                logger.warning(f"Failed to notify evicted session: {e}")

        # Clean up the evicted session
        await clear_chat_session(eviction_candidate)  # Clear Redis chat history
        await cleanup_session(eviction_candidate, user_id, evicted_conversation_id)


async def cleanup_abandoned_tool_executions():
    """Periodically clean up abandoned tool executions that were never completed"""
    while True:
        try:
            await asyncio.sleep(60)  # Run every minute
            tool_result_handler.cleanup_old_executions(max_age_seconds=60)

            pending_count = tool_result_handler.get_pending_count()
            if pending_count > 0:
                logger.debug(f"Currently {pending_count} tool executions pending")

        except Exception as e:
            logger.error(f"Error in tool execution cleanup task: {str(e)}")


async def cleanup_stale_sessions():
    """Periodically clean up stale sessions that haven't been active"""
    while True:
        try:
            await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)

            current_time = time.time()
            cutoff_time = current_time - SESSION_TIMEOUT_SECONDS

            # Find all stale sessions
            stale_sessions = await get_stale_sessions(cutoff_time)

            if stale_sessions:
                logger.info(f"Found {len(stale_sessions)} stale sessions to clean up")

                for session_id in stale_sessions:
                    # Extract user_id and conversation_id from session_id if possible
                    user_id = None
                    conversation_id = None

                    # Session ID format: ws_{user_id}_conv_{conversation_id} or ws_{user_id}_new_{timestamp}
                    parts = session_id.split('_')
                    if len(parts) >= 2:
                        user_id = parts[1]
                    if len(parts) >= 4 and parts[2] == 'conv':
                        try:
                            conversation_id = int(parts[3])
                        except ValueError:
                            pass

                    stale_timestamp = await get_session_timestamp(session_id) or 0
                    logger.info(f"Cleaning up stale session: {session_id} (inactive for {int(current_time - stale_timestamp)} seconds)")
                    await cleanup_session(session_id, user_id, conversation_id)

        except Exception as e:
            logger.error(f"Error during stale session cleanup: {str(e)}")
            logger.error(traceback.format_exc())
            # Continue running even if an error occurs
            continue
