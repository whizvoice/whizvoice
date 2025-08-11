# Redis Session Management Migration Guide

## Overview
This guide shows how to replace local dictionary usage with Redis-backed managers for distributed session support.

## Required Changes in app.py

### 1. Import the helper functions
```python
from redis_helpers import (
    # Chat session functions
    get_chat_messages, add_chat_message, set_chat_messages, clear_chat_session,
    # User session functions
    get_user_sessions, add_user_session, remove_user_session, get_all_user_sessions,
    # Session timestamp functions
    update_session_activity, get_session_timestamp, remove_session_timestamp, 
    get_stale_sessions, get_all_session_timestamps,
    # Active request functions
    add_active_request, remove_active_request, get_active_requests, clear_active_requests,
    # Session mapping functions
    set_session_mapping, get_real_id, get_optimistic_id, clear_session_mappings,
    init_session_mappings,
    # Utility functions
    get_total_session_count, get_user_session_count,
    # Module initialization
    set_managers_and_storage
)
```

### 2. Initialize the helpers module after Redis connection
In `init_redis()` function, after creating managers:
```python
# Initialize the helpers module with managers and local storage
local_storage = {
    "chat_sessions": chat_sessions,
    "user_sessions": user_sessions,
    "session_timestamps": session_timestamps,
    "active_requests": active_requests,
    "session_mappings": session_mappings
}
locks = {
    "chat_sessions_lock": chat_sessions_lock,
    "user_sessions_lock": user_sessions_lock,
    "session_timestamps_lock": session_timestamps_lock,
    "active_requests_lock": active_requests_lock,
    "session_mappings_lock": session_mappings_lock
}
set_managers_and_storage(redis_managers, local_storage, locks)
```

## Replacement Patterns

### Chat Sessions
```python
# OLD: chat_sessions[session_id] = []
# NEW: await set_chat_messages(session_id, [])

# OLD: chat_sessions[session_id].append(message)
# NEW: await add_chat_message(session_id, message)

# OLD: messages = chat_sessions[session_id]
# NEW: messages = await get_chat_messages(session_id)

# OLD: del chat_sessions[session_id]
# NEW: await clear_chat_session(session_id)

# OLD: len(chat_sessions[session_id])
# NEW: len(await get_chat_messages(session_id))
```

### User Sessions
```python
# OLD: user_sessions[user_id] = []
# NEW: # Not needed, add_user_session handles initialization

# OLD: user_sessions[user_id].append(session_id)
# NEW: await add_user_session(user_id, session_id)

# OLD: user_sessions[user_id].remove(session_id)
# NEW: await remove_user_session(user_id, session_id)

# OLD: if session_id in user_sessions[user_id]
# NEW: if session_id in await get_user_sessions(user_id)

# OLD: sessions = list(user_sessions[user_id])
# NEW: sessions = await get_user_sessions(user_id)

# OLD: del user_sessions[user_id]
# NEW: # Handled automatically by remove_user_session
```

### Session Timestamps
```python
# OLD: session_timestamps[session_id] = time.time()
# NEW: await update_session_activity(session_id)

# OLD: del session_timestamps[session_id]
# NEW: await remove_session_timestamp(session_id)

# OLD: timestamp = session_timestamps.get(session_id, 0)
# NEW: timestamp = await get_session_timestamp(session_id) or 0

# OLD: for session_id, timestamp in session_timestamps.items()
# NEW: for session_id, timestamp in (await get_all_session_timestamps()).items()
```

### Active Requests
```python
# OLD: active_requests[session_id] = set()
# NEW: # Not needed, add_active_request handles initialization

# OLD: active_requests[session_id].add(request_id)
# NEW: await add_active_request(session_id, request_id)

# OLD: active_requests[session_id].discard(request_id)
# NEW: await remove_active_request(session_id, request_id)

# OLD: active_requests[session_id].clear()
# NEW: await clear_active_requests(session_id)

# OLD: del active_requests[session_id]
# NEW: await clear_active_requests(session_id)

# OLD: len(active_requests[session_id])
# NEW: len(await get_active_requests(session_id))

# OLD: session_id in active_requests and active_requests[session_id]
# NEW: bool(await get_active_requests(session_id))
```

### Session Mappings
```python
# OLD: session_mappings[session_id] = {"optimistic_to_real": {}, "real_to_optimistic": {}}
# NEW: await init_session_mappings(session_id)

# OLD: session_mappings[session_id]["optimistic_to_real"][client_id] = real_id
# NEW: await set_session_mapping(session_id, client_id, real_id)

# OLD: mappings = session_mappings.get(session_id, {})
#      real_id = mappings.get("optimistic_to_real", {}).get(client_id)
# NEW: real_id = await get_real_id(session_id, client_id)

# OLD: del session_mappings[session_id]
# NEW: await clear_session_mappings(session_id)
```

## Lock Removal
Since Redis operations are atomic, many lock usages can be removed:
```python
# OLD:
async with chat_sessions_lock:
    chat_sessions[session_id] = messages

# NEW:
await set_chat_messages(session_id, messages)
# No lock needed!
```

## Testing
After migration, test:
1. Single server functionality (Redis as cache)
2. Multi-server message broadcasting
3. Session failover between workers
4. Cleanup of stale sessions
5. Performance under load

## Rollback Plan
If issues arise, you can:
1. Set `redis_managers = None` to fall back to local storage
2. Or simply not initialize Redis (it will use local storage automatically)