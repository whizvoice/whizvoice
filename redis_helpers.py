"""
Helper functions for Redis-backed session management with fallback to local storage
"""
from typing import List, Dict, Set, Optional, Any
import asyncio
import logging

logger = logging.getLogger(__name__)

# These will be imported and set by app.py
redis_managers = None
# Fallback local storage
chat_sessions = {}
user_sessions = {}
session_timestamps = {}
request_states = {}  # New: track request states
# Locks
chat_sessions_lock = asyncio.Lock()
user_sessions_lock = asyncio.Lock()
session_timestamps_lock = asyncio.Lock()
request_states_lock = asyncio.Lock()  # New: lock for request states


def set_managers_and_storage(managers, local_storage, locks):
    """Initialize the module with Redis managers and local storage references"""
    global redis_managers, chat_sessions, user_sessions, session_timestamps
    global request_states
    global chat_sessions_lock, user_sessions_lock, session_timestamps_lock
    global request_states_lock
    
    redis_managers = managers
    chat_sessions = local_storage.get("chat_sessions", {})
    user_sessions = local_storage.get("user_sessions", {})
    session_timestamps = local_storage.get("session_timestamps", {})
    request_states = local_storage.get("request_states", {})
    
    chat_sessions_lock = locks.get("chat_sessions_lock")
    user_sessions_lock = locks.get("user_sessions_lock")
    session_timestamps_lock = locks.get("session_timestamps_lock")
    request_states_lock = locks.get("request_states_lock")


# Chat Session Management
async def get_chat_messages(session_id: str) -> List[Dict]:
    """Get chat messages for a session from Redis or local storage"""
    if redis_managers:
        messages = await redis_managers["chat_sessions"].get(session_id)
        return messages if messages else []
    else:
        async with chat_sessions_lock:
            return chat_sessions.get(session_id, [])


async def add_chat_message(session_id: str, message: Dict):
    """Add a chat message to a session in Redis or local storage"""
    content_preview = message.get('content', '')[:100] if message.get('content') else 'N/A'
    logger.info(f"[REDIS_DEBUG] add_chat_message: session_id={session_id}, role={message.get('role')}, content={content_preview}")
    if redis_managers:
        await redis_managers["chat_sessions"].add_message(session_id, message)
    else:
        async with chat_sessions_lock:
            if session_id not in chat_sessions:
                chat_sessions[session_id] = []
            chat_sessions[session_id].append(message)


async def set_chat_messages(session_id: str, messages: List[Dict]):
    """Set all chat messages for a session in Redis or local storage"""
    logger.info(f"[REDIS_DEBUG] set_chat_messages: session_id={session_id}, setting {len(messages)} messages")
    if messages:
        first_content = messages[0].get('content', '')[:100] if messages[0].get('content') else 'N/A'
        logger.info(f"[REDIS_DEBUG] First message: role={messages[0].get('role')}, content={first_content}")
    if redis_managers:
        await redis_managers["chat_sessions"].set(session_id, messages)
    else:
        async with chat_sessions_lock:
            chat_sessions[session_id] = messages


async def clear_chat_session(session_id: str):
    """Clear chat session from Redis or local storage"""
    logger.info(f"[REDIS_DEBUG] clear_chat_session: Clearing session {session_id}")
    if redis_managers:
        await redis_managers["chat_sessions"].clear(session_id)
        logger.info(f"[REDIS_DEBUG] clear_chat_session: Redis key deleted for {session_id}")
    else:
        async with chat_sessions_lock:
            if session_id in chat_sessions:
                del chat_sessions[session_id]
                logger.info(f"[REDIS_DEBUG] clear_chat_session: Local session deleted for {session_id}")


# User Session Management
async def get_user_sessions(user_id: str) -> List[str]:
    """Get all session IDs for a user"""
    if redis_managers:
        return await redis_managers["user_sessions"].get_sessions(user_id)
    else:
        async with user_sessions_lock:
            return list(user_sessions.get(user_id, []))


async def add_user_session(user_id: str, session_id: str):
    """Add a session to a user"""
    if redis_managers:
        await redis_managers["user_sessions"].add_session(user_id, session_id)
    else:
        async with user_sessions_lock:
            if user_id not in user_sessions:
                user_sessions[user_id] = set()
            user_sessions[user_id].add(session_id)


async def remove_user_session(user_id: str, session_id: str):
    """Remove a session from a user"""
    if redis_managers:
        await redis_managers["user_sessions"].remove_session(user_id, session_id)
    else:
        async with user_sessions_lock:
            if user_id in user_sessions:
                user_sessions[user_id].discard(session_id)
                if not user_sessions[user_id]:
                    del user_sessions[user_id]


async def get_all_user_sessions() -> Dict[str, List[str]]:
    """Get all user sessions (for monitoring)"""
    if redis_managers:
        return await redis_managers["user_sessions"].get_all()
    else:
        async with user_sessions_lock:
            return {user_id: list(sessions) for user_id, sessions in user_sessions.items()}


# Session Timestamp Management
async def update_session_activity(session_id: str, timestamp: Optional[float] = None):
    """Update session activity timestamp"""
    if redis_managers:
        await redis_managers["session_timestamps"].update(session_id, timestamp)
    else:
        import time
        async with session_timestamps_lock:
            session_timestamps[session_id] = timestamp or time.time()


async def get_session_timestamp(session_id: str) -> Optional[float]:
    """Get session activity timestamp"""
    if redis_managers:
        return await redis_managers["session_timestamps"].get(session_id)
    else:
        async with session_timestamps_lock:
            return session_timestamps.get(session_id)


async def remove_session_timestamp(session_id: str):
    """Remove session timestamp"""
    if redis_managers:
        await redis_managers["session_timestamps"].delete(session_id)
    else:
        async with session_timestamps_lock:
            if session_id in session_timestamps:
                del session_timestamps[session_id]


async def get_stale_sessions(cutoff_time: float) -> List[str]:
    """Get sessions older than cutoff time"""
    if redis_managers:
        return await redis_managers["session_timestamps"].get_stale_sessions(cutoff_time)
    else:
        async with session_timestamps_lock:
            return [
                session_id for session_id, timestamp in session_timestamps.items()
                if timestamp < cutoff_time
            ]


async def get_all_session_timestamps() -> Dict[str, float]:
    """Get all session timestamps"""
    if redis_managers:
        return await redis_managers["session_timestamps"].get_all()
    else:
        async with session_timestamps_lock:
            return dict(session_timestamps)


# Active Request Management
async def add_active_request(session_id: str, request_id: str):
    """Add an active request for a session"""
    if redis_managers and "active_requests" in redis_managers:
        await redis_managers["active_requests"].add(session_id, request_id)
    # No local fallback - active requests are Redis-only


async def remove_active_request(session_id: str, request_id: str):
    """Remove an active request from a session"""
    if redis_managers and "active_requests" in redis_managers:
        await redis_managers["active_requests"].remove(session_id, request_id)
    # No local fallback - active requests are Redis-only


async def get_active_requests(session_id: str) -> Set[str]:
    """Get all active requests for a session"""
    if redis_managers and "active_requests" in redis_managers:
        return await redis_managers["active_requests"].get(session_id)
    else:
        return set()  # No local fallback - active requests are Redis-only


async def clear_active_requests(session_id: str):
    """Clear all active requests for a session"""
    if redis_managers and "active_requests" in redis_managers:
        await redis_managers["active_requests"].clear(session_id)
    # No local fallback - active requests are Redis-only


# Request State Tracking
async def set_request_state(request_id: str, state: str, metadata: Optional[Dict[str, Any]] = None):
    """Set the state of a request (pending, completed, failed, timeout)"""
    state_data = {
        "state": state,
        "timestamp": asyncio.get_event_loop().time(),
        "metadata": metadata or {}
    }
    
    if redis_managers and "request_states" in redis_managers:
        await redis_managers["request_states"].set(request_id, state_data)
    else:
        async with request_states_lock:
            request_states[request_id] = state_data


async def get_request_state(request_id: str) -> Optional[Dict[str, Any]]:
    """Get the state of a request"""
    if redis_managers and "request_states" in redis_managers:
        return await redis_managers["request_states"].get(request_id)
    else:
        async with request_states_lock:
            return request_states.get(request_id)


async def get_all_request_states(request_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """Get states for multiple requests"""
    result = {}
    for request_id in request_ids:
        state = await get_request_state(request_id)
        if state:
            result[request_id] = state
    return result


# Session Mapping Management (for optimistic IDs)
async def set_session_mapping(session_id: str, client_id: int, real_id: int):
    """Set optimistic ID mapping for a session"""
    if redis_managers:
        await redis_managers["session_mappings"].set_mapping(session_id, client_id, real_id)


async def get_real_id(session_id: str, client_id: int) -> Optional[int]:
    """Get real ID from optimistic client ID"""
    if redis_managers:
        return await redis_managers["session_mappings"].get_real_id(session_id, client_id)
    return None


async def get_optimistic_id(session_id: str, real_id: int) -> Optional[int]:
    """Get optimistic client ID from real ID"""
    if redis_managers:
        return await redis_managers["session_mappings"].get_optimistic_id(session_id, real_id)
    return None


async def clear_session_mappings(session_id: str):
    """Clear all mappings for a session"""
    if redis_managers:
        await redis_managers["session_mappings"].clear(session_id)




# Session count helpers
async def get_total_session_count() -> int:
    """Get total number of active sessions"""
    if redis_managers:
        timestamps = await redis_managers["session_timestamps"].get_all()
        return len(timestamps)
    else:
        async with session_timestamps_lock:
            return len(session_timestamps)


async def get_user_session_count(user_id: str) -> int:
    """Get number of sessions for a specific user"""
    sessions = await get_user_sessions(user_id)
    return len(sessions)