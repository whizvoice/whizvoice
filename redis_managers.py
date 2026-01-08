"""
Redis managers for WhizVoice - Handles state that needs to be shared across servers
"""
import json
import time
import os
import asyncio
from typing import Dict, List, Set, Optional, Tuple, Any, Union
from fastapi import WebSocket
import redis.asyncio as redis
import logging

logger = logging.getLogger(__name__)

# Server ID for multi-server deployments
SERVER_ID = os.getenv("SERVER_ID", f"server_{os.getpid()}")


class ChatSessionManager:
    """Manages chat message history in Redis using ZSET for automatic timestamp ordering"""

    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.ttl = 900  # 15 minutes

    def _timestamp_to_score(self, timestamp: Optional[str]) -> float:
        """Convert ISO timestamp string to epoch float for ZSET score"""
        if timestamp:
            from datetime import datetime
            import re
            try:
                # Normalize the timestamp for Python's fromisoformat()
                ts = timestamp.replace('Z', '+00:00')

                # Fix fractional seconds: Python < 3.11 requires exactly 3 or 6 digits
                # Match pattern like ".4+00:00" or ".40+00:00" and pad to 3 digits
                match = re.match(r'^(.+\.)(\d{1,6})([+-].*)$', ts)
                if match:
                    prefix, frac, suffix = match.groups()
                    # Pad or truncate to exactly 6 digits for consistency
                    frac_padded = frac.ljust(6, '0')[:6]
                    ts = f"{prefix}{frac_padded}{suffix}"

                dt = datetime.fromisoformat(ts)
                return dt.timestamp()
            except (ValueError, AttributeError) as e:
                logger.warning(f"⚠️ _timestamp_to_score: Invalid timestamp format: {timestamp}, error: {e}, using time.time() fallback")
        else:
            logger.warning(f"⚠️ _timestamp_to_score: No timestamp provided, using time.time() fallback")
        # Fallback to current time if no valid timestamp
        return time.time()

    # --- Session Redirect Methods (for optimistic ID migration) ---

    async def set_redirect(self, old_session_id: str, new_session_id: str):
        """Store a redirect from old session_id to new session_id.

        This is used when migrating from optimistic to real conversation IDs.
        The redirect ensures that any operations on the old session_id
        are automatically forwarded to the new session_id.
        """
        key = f"session_redirect:{old_session_id}"
        await self.redis.set(key, new_session_id, ex=3600)  # 1 hour TTL
        logger.info(f"Set session redirect: {old_session_id} → {new_session_id}")

    async def get_redirect(self, session_id: str) -> Optional[str]:
        """Get redirect target for a session_id, if one exists."""
        key = f"session_redirect:{session_id}"
        result = await self.redis.get(key)
        if result:
            return result.decode() if isinstance(result, bytes) else result
        return None

    async def resolve_session_id(self, session_id: str) -> str:
        """Resolve a session_id to its canonical form (following redirects).

        If a redirect exists, returns the target session_id.
        Otherwise returns the original session_id.
        """
        redirect = await self.get_redirect(session_id)
        if redirect:
            logger.info(f"Resolved session redirect: {session_id} → {redirect}")
            return redirect
        return session_id

    async def rename_session(self, old_session_id: str, new_session_id: str) -> bool:
        """Rename a session with redirect-first for safety.

        This atomically:
        1. Sets a redirect from old to new (FIRST - critical for race condition handling)
        2. Renames the Redis key if it exists

        Any in-flight operations using the old session_id will be redirected.
        """
        old_key = f"chat_session:{old_session_id}"
        new_key = f"chat_session:{new_session_id}"

        # CRITICAL: Set redirect FIRST to minimize race window
        await self.set_redirect(old_session_id, new_session_id)

        # Then try to rename (best effort)
        try:
            if await self.redis.exists(old_key) and not await self.redis.exists(new_key):
                await self.redis.rename(old_key, new_key)
                logger.info(f"Renamed session {old_session_id} → {new_session_id}")
            elif await self.redis.exists(new_key):
                logger.info(f"Session {new_session_id} already exists, redirect set only")
            else:
                logger.info(f"Old session {old_session_id} doesn't exist, redirect set only")
        except Exception as e:
            logger.warning(f"Rename failed (redirect still set): {e}")

        return True

    # --- End Session Redirect Methods ---

    async def get(self, session_id: str, include_cancelled: bool = False) -> List[Dict]:
        """Get chat history for a session, ordered by timestamp"""
        # Resolve any redirect first
        session_id = await self.resolve_session_id(session_id)
        key = f"chat_session:{session_id}"
        # ZRANGE returns members in score order (lowest to highest = oldest to newest)
        members = await self.redis.zrange(key, 0, -1)
        messages = []
        for member in members:
            msg = json.loads(member)
            # Filter out cancelled messages unless explicitly requested
            if include_cancelled or not msg.get("_cancelled"):
                messages.append(msg)
        return messages

    async def set(self, session_id: str, messages: List[Dict]):
        """Set entire chat history using ZSET"""
        # Resolve any redirect first
        session_id = await self.resolve_session_id(session_id)
        key = f"chat_session:{session_id}"
        # Delete existing data
        await self.redis.delete(key)

        if messages:
            logger.info(f"📝 set() called for session {session_id} with {len(messages)} messages")
            # Add all messages with their timestamps as scores
            for i, msg in enumerate(messages):
                timestamp = msg.get("_timestamp")
                score = self._timestamp_to_score(timestamp)
                role = msg.get("role", "unknown")
                content_preview = str(msg.get("content", ""))[:50]
                logger.info(f"📝 set() msg[{i}]: role={role}, _timestamp={timestamp}, score={score}, content={content_preview}...")
                await self.redis.zadd(key, {json.dumps(msg): score})

            await self.redis.expire(key, self.ttl)

    async def set_messages(self, session_id: str, messages: List[Dict]):
        """Alias for set() method - for backward compatibility"""
        await self.set(session_id, messages)

    async def get_messages(self, session_id: str) -> List[Dict]:
        """Alias for get() method - for backward compatibility"""
        return await self.get(session_id)

    async def add_message(self, session_id: str, message: Union[Dict, List[Dict]], timestamp: Union[str, List[str]] = None, request_id: str = None):
        """Add message(s) to the session using ZSET for automatic ordering.

        Args:
            session_id: The session ID
            message: Single message dict OR list of message dicts for atomic batch add
            timestamp: Single timestamp OR list of timestamps (one per message if batch)
            request_id: Request ID (applied to all messages)

        When a list of messages is passed, they are added atomically using a Redis
        pipeline with transaction=True. This prevents race conditions where another
        worker might read partial state between writes.

        Includes check-after-write pattern to handle race conditions where
        a redirect is created while the write is in progress.
        """
        original_session_id = session_id
        resolved_id = await self.resolve_session_id(session_id)
        key = f"chat_session:{resolved_id}"

        if isinstance(message, list):
            # ATOMIC BATCH: Use pipeline with transaction for multiple messages
            messages = message
            timestamps = timestamp if isinstance(timestamp, list) else [timestamp] * len(messages)

            # Prepare all messages with metadata
            prepared_messages = []
            for msg, ts in zip(messages, timestamps):
                msg_copy = msg.copy()
                if ts:
                    msg_copy["_timestamp"] = ts
                if request_id:
                    msg_copy["_request_id"] = request_id
                score = self._timestamp_to_score(ts)
                msg_json = json.dumps(msg_copy)
                prepared_messages.append((msg_json, score))

            # Atomic write using pipeline
            async with self.redis.pipeline(transaction=True) as pipe:
                for msg_json, score in prepared_messages:
                    pipe.zadd(key, {msg_json: score})
                pipe.expire(key, self.ttl)
                await pipe.execute()

            # CHECK-AFTER-WRITE: Handle race condition where redirect was created during our write
            if resolved_id == original_session_id:
                current_redirect = await self.resolve_session_id(original_session_id)
                if current_redirect != resolved_id:
                    # Redirect appeared! Duplicate all messages to correct location
                    new_key = f"chat_session:{current_redirect}"
                    async with self.redis.pipeline(transaction=True) as pipe:
                        for msg_json, score in prepared_messages:
                            pipe.zadd(new_key, {msg_json: score})
                        pipe.expire(new_key, self.ttl)
                        await pipe.execute()
                    logger.info(f"Check-after-write: duplicated {len(messages)} messages to {current_redirect} (orphan at {resolved_id} will expire)")
        else:
            # SINGLE MESSAGE: Original logic
            # Add metadata to message
            if timestamp:
                message["_timestamp"] = timestamp
            if request_id:
                message["_request_id"] = request_id

            # Convert timestamp to epoch float for score
            score = self._timestamp_to_score(timestamp)
            msg_json = json.dumps(message)

            await self.redis.zadd(key, {msg_json: score})
            await self.redis.expire(key, self.ttl)

            # CHECK-AFTER-WRITE: Handle race condition where redirect was created during our write
            if resolved_id == original_session_id:  # We used original ID (wasn't already redirected)
                current_redirect = await self.resolve_session_id(original_session_id)
                if current_redirect != resolved_id:
                    # Redirect appeared! Duplicate message to correct location
                    new_key = f"chat_session:{current_redirect}"
                    await self.redis.zadd(new_key, {msg_json: score})
                    await self.redis.expire(new_key, self.ttl)
                    logger.info(f"Check-after-write: duplicated message to {current_redirect} (orphan at {resolved_id} will expire)")

        # Trim if too long (keep messages with highest scores = most recent by timestamp)
        count = await self.redis.zcard(key)
        if count > 100:
            # Remove oldest messages (lowest scores)
            await self.redis.zremrangebyrank(key, 0, count - 101)

    async def append(self, session_id: str, message: Dict):
        """Append a message to chat history - backward compatible, uses current time as timestamp"""
        await self.add_message(session_id, message)

    async def extend(self, session_id: str, new_messages: List[Dict]):
        """Extend chat history with multiple messages"""
        # Resolve any redirect first
        session_id = await self.resolve_session_id(session_id)
        key = f"chat_session:{session_id}"
        for msg in new_messages:
            timestamp = msg.get("_timestamp")
            score = self._timestamp_to_score(timestamp)
            await self.redis.zadd(key, {json.dumps(msg): score})

        await self.redis.expire(key, self.ttl)

        # Trim if too long
        count = await self.redis.zcard(key)
        if count > 100:
            await self.redis.zremrangebyrank(key, 0, count - 101)

    async def delete(self, session_id: str):
        """Delete a session"""
        # Resolve any redirect first
        session_id = await self.resolve_session_id(session_id)
        await self.redis.delete(f"chat_session:{session_id}")

    async def clear(self, session_id: str):
        """Alias for delete() method - for backward compatibility"""
        await self.delete(session_id)

    async def exists(self, session_id: str) -> bool:
        """Check if session exists"""
        # Resolve any redirect first
        session_id = await self.resolve_session_id(session_id)
        return await self.redis.exists(f"chat_session:{session_id}")

    async def mark_cancelled(self, session_id: str, request_id: str):
        """Mark all messages with given request_id as cancelled"""
        # Resolve any redirect first
        session_id = await self.resolve_session_id(session_id)
        key = f"chat_session:{session_id}"
        members_with_scores = await self.redis.zrange(key, 0, -1, withscores=True)
        for member, score in members_with_scores:
            msg = json.loads(member)
            if msg.get("_request_id") == request_id:
                # Remove old message and add updated one with same score
                await self.redis.zrem(key, member)
                msg["_cancelled"] = True
                await self.redis.zadd(key, {json.dumps(msg): score})

    async def update_pending_result_timestamp(self, session_id: str, tool_use_ids: List[str], new_timestamp: str):
        """Update timestamp for pending tool_result message to sync with DB.

        In a ZSET, the member (JSON string) is the key, so we must:
        1. Remove the old member
        2. Update _timestamp in the message
        3. Add the new member with new score
        """
        # Resolve any redirect first
        session_id = await self.resolve_session_id(session_id)
        key = f"chat_session:{session_id}"
        members_with_scores = await self.redis.zrange(key, 0, -1, withscores=True)

        for member, old_score in members_with_scores:
            msg = json.loads(member)
            if msg.get("role") == "user" and isinstance(msg.get("content"), list):
                # Check if this message contains any of our pending tool_results
                has_pending = any(
                    block.get("tool_use_id") in tool_use_ids and block.get("content") == "Result pending..."
                    for block in msg.get("content", [])
                    if isinstance(block, dict)
                )
                if has_pending:
                    # Remove old, update timestamp, add new
                    await self.redis.zrem(key, member)
                    msg["_timestamp"] = new_timestamp
                    new_score = self._timestamp_to_score(new_timestamp)
                    await self.redis.zadd(key, {json.dumps(msg): new_score})
                    logger.info(f"Updated pending result timestamp in Redis: {new_timestamp}")
                    return True
        return False

    async def update_tool_results(self, session_id: str, tool_updates: Dict[str, str]) -> int:
        """Update pending tool_results with actual results.

        Only removes and re-adds the specific messages that contain updated tool_results,
        rather than replacing all messages in the zset.

        Args:
            session_id: The session ID
            tool_updates: Dict mapping tool_use_id -> actual result content (JSON string)

        Returns:
            Number of messages updated
        """
        # Resolve any redirect first
        session_id = await self.resolve_session_id(session_id)
        key = f"chat_session:{session_id}"
        members_with_scores = await self.redis.zrange(key, 0, -1, withscores=True)

        updated_count = 0
        tool_ids_to_update = set(tool_updates.keys())

        for member, score in members_with_scores:
            msg = json.loads(member)
            if msg.get("role") == "user" and isinstance(msg.get("content"), list):
                # Check if this message contains any pending tool_results we need to update
                needs_update = False
                for block in msg.get("content", []):
                    if isinstance(block, dict) and \
                       block.get("type") == "tool_result" and \
                       block.get("tool_use_id") in tool_ids_to_update:
                        needs_update = True
                        break

                if needs_update:
                    # Remove old message
                    await self.redis.zrem(key, member)

                    # Update the tool_result blocks
                    for i, block in enumerate(msg["content"]):
                        if isinstance(block, dict) and \
                           block.get("type") == "tool_result" and \
                           block.get("tool_use_id") in tool_updates:
                            tool_use_id = block["tool_use_id"]
                            msg["content"][i] = {
                                "type": "tool_result",
                                "tool_use_id": tool_use_id,
                                "content": tool_updates[tool_use_id]
                            }
                            logger.info(f"Updated tool_result for tool_use_id={tool_use_id}")

                    # Add updated message back with same score
                    await self.redis.zadd(key, {json.dumps(msg): score})
                    updated_count += 1

                    # Remove updated IDs from the set we're looking for
                    for block in msg.get("content", []):
                        if isinstance(block, dict) and block.get("tool_use_id") in tool_ids_to_update:
                            tool_ids_to_update.discard(block.get("tool_use_id"))

                    # Exit early if we've found all the tool_results
                    if not tool_ids_to_update:
                        break

        return updated_count


class UserSessionManager:
    """Manages user -> sessions mapping in Redis"""
    
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        
    async def add_session(self, user_id: str, session_id: str):
        """Add a session for a user"""
        await self.redis.sadd(f"user_sessions:{user_id}", session_id)
        # Set expiry on the set
        await self.redis.expire(f"user_sessions:{user_id}", 3600)  # 1 hour
        
    async def remove_session(self, user_id: str, session_id: str):
        """Remove a session from a user"""
        await self.redis.srem(f"user_sessions:{user_id}", session_id)
        
        # Clean up empty sets
        count = await self.redis.scard(f"user_sessions:{user_id}")
        if count == 0:
            await self.redis.delete(f"user_sessions:{user_id}")
    
    async def get_sessions(self, user_id: str) -> List[str]:
        """Get all sessions for a user"""
        sessions = await self.redis.smembers(f"user_sessions:{user_id}")
        return list(sessions) if sessions else []
    
    async def exists(self, user_id: str) -> bool:
        """Check if user has any sessions"""
        return await self.redis.exists(f"user_sessions:{user_id}")
    
    async def session_count(self, user_id: str) -> int:
        """Get count of user's sessions"""
        return await self.redis.scard(f"user_sessions:{user_id}")


class SessionTimestampManager:
    """Manages session activity timestamps in Redis"""
    
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        
    async def update(self, session_id: str, timestamp: Optional[float] = None):
        """Update session timestamp"""
        if timestamp is None:
            timestamp = time.time()
        await self.redis.set(
            f"session_timestamp:{session_id}",
            timestamp,
            ex=900  # 15 minutes
        )
    
    async def get(self, session_id: str) -> Optional[float]:
        """Get session timestamp"""
        value = await self.redis.get(f"session_timestamp:{session_id}")
        return float(value) if value else None
    
    async def delete(self, session_id: str):
        """Delete timestamp"""
        await self.redis.delete(f"session_timestamp:{session_id}")
    
    async def remove(self, session_id: str):
        """Alias for delete() method - for backward compatibility"""
        await self.delete(session_id)
    
    async def get_all(self) -> Dict[str, float]:
        """Get all timestamps (for cleanup)"""
        # Use SCAN to find all timestamp keys
        timestamps = {}
        cursor = 0
        while True:
            cursor, keys = await self.redis.scan(
                cursor, match="session_timestamp:*", count=100
            )
            for key in keys:
                # Handle both bytes (old redis-py) and str (new redis-py)
                key_str = key.decode() if isinstance(key, bytes) else key
                session_id = key_str.replace("session_timestamp:", "")
                value = await self.redis.get(key)
                if value:
                    timestamps[session_id] = float(value)
            if cursor == 0:
                break
        return timestamps
    
    async def get_stale_sessions(self, cutoff_time: float) -> List[str]:
        """Get session IDs with timestamps older than cutoff time"""
        stale_sessions = []
        all_timestamps = await self.get_all()
        for session_id, timestamp in all_timestamps.items():
            if timestamp < cutoff_time:
                stale_sessions.append(session_id)
        return stale_sessions


class ActiveRequestManager:
    """Manages active request tracking in Redis"""
    
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        
    async def add(self, session_id: str, request_id: str):
        """Add an active request"""
        await self.redis.sadd(f"active_requests:{session_id}", request_id)
        await self.redis.expire(f"active_requests:{session_id}", 300)  # 5 minutes
    
    async def remove(self, session_id: str, request_id: str):
        """Remove an active request"""
        await self.redis.srem(f"active_requests:{session_id}", request_id)
    
    async def get_all(self, session_id: str) -> Set[str]:
        """Get all active requests for a session"""
        requests = await self.redis.smembers(f"active_requests:{session_id}")
        return set(requests) if requests else set()
    
    async def get(self, session_id: str) -> Set[str]:
        """Alias for get_all() method - for backward compatibility"""
        return await self.get_all(session_id)
    
    async def clear(self, session_id: str):
        """Clear all active requests for a session"""
        await self.redis.delete(f"active_requests:{session_id}")
    
    async def exists(self, session_id: str) -> bool:
        """Check if session has active requests"""
        count = await self.redis.scard(f"active_requests:{session_id}")
        return count > 0


class SessionMappingManager:
    """Manages optimistic ID mappings in Redis"""
    
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        
    async def set_mapping(self, session_id: str, client_id: int, real_id: int):
        """Set optimistic to real ID mapping"""
        # CRITICAL VALIDATION: Check if this real_id already has a different optimistic mapping
        existing_optimistic = await self.get_optimistic_id(session_id, real_id)
        if existing_optimistic is not None and existing_optimistic != client_id:
            logger.error(f"CRITICAL: Multiple optimistic IDs mapping to same real ID! "
                        f"Session {session_id}: existing optimistic {existing_optimistic} "
                        f"conflicts with new optimistic {client_id} for real ID {real_id}")
            # Raise an exception to prevent data corruption
            raise ValueError(f"Real conversation {real_id} already mapped to optimistic ID {existing_optimistic}, "
                           f"cannot map to {client_id}")
        
        # Store both directions
        await self.redis.hset(
            f"session_mapping:{session_id}:opt_to_real",
            str(client_id),
            str(real_id)
        )
        await self.redis.hset(
            f"session_mapping:{session_id}:real_to_opt",
            str(real_id),
            str(client_id)
        )
        # Set expiry
        await self.redis.expire(f"session_mapping:{session_id}:opt_to_real", 3600)
        await self.redis.expire(f"session_mapping:{session_id}:real_to_opt", 3600)
    
    async def get_real_id(self, session_id: str, client_id: int) -> Optional[int]:
        """Get real ID from optimistic ID"""
        value = await self.redis.hget(
            f"session_mapping:{session_id}:opt_to_real",
            str(client_id)
        )
        return int(value) if value else None
    
    async def get_optimistic_id(self, session_id: str, real_id: int) -> Optional[int]:
        """Get optimistic ID from real ID"""
        value = await self.redis.hget(
            f"session_mapping:{session_id}:real_to_opt",
            str(real_id)
        )
        return int(value) if value else None
    
    async def delete(self, session_id: str):
        """Delete all mappings for a session"""
        await self.redis.delete(f"session_mapping:{session_id}:opt_to_real")
        await self.redis.delete(f"session_mapping:{session_id}:real_to_opt")
    
    async def clear(self, session_id: str):
        """Alias for delete() method - for backward compatibility"""
        await self.delete(session_id)



class RequestStateManager:
    """Manages request state tracking in Redis"""
    
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.ttl = 3600  # 1 hour TTL for request states
    
    async def set(self, request_id: str, state_data: Dict[str, Any]):
        """Set the state of a request"""
        await self.redis.set(
            f"request_state:{request_id}",
            json.dumps(state_data),
            ex=self.ttl
        )
    
    async def get(self, request_id: str) -> Optional[Dict[str, Any]]:
        """Get the state of a request"""
        data = await self.redis.get(f"request_state:{request_id}")
        return json.loads(data) if data else None
    
    async def delete(self, request_id: str):
        """Delete a request state"""
        await self.redis.delete(f"request_state:{request_id}")
    
    async def exists(self, request_id: str) -> bool:
        """Check if a request state exists"""
        return await self.redis.exists(f"request_state:{request_id}")
    
    async def get_multiple(self, request_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        """Get states for multiple requests"""
        result = {}
        for request_id in request_ids:
            state = await self.get(request_id)
            if state:
                result[request_id] = state
        return result


class RequestMessageTracker:
    """Tracks which message IDs each request is responding to"""
    
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.ttl = 3600  # 1 hour TTL
    
    async def set_messages(self, request_id: str, message_ids: List[int], 
                          conversation_id: int, stream_object: Optional[Any] = None):
        """Store message IDs for a request"""
        data = {
            "message_ids": message_ids,
            "conversation_id": conversation_id,
            "timestamp": time.time(),
            "status": "active"
        }
        await self.redis.set(
            f"request_messages:{request_id}",
            json.dumps(data),
            ex=self.ttl
        )
        # Stream object is stored locally via LocalObjectManager
    
    async def get_messages(self, request_id: str) -> Optional[Dict[str, Any]]:
        """Get message IDs for a request"""
        data = await self.redis.get(f"request_messages:{request_id}")
        return json.loads(data) if data else None
    
    async def get_by_conversation(self, conversation_id: int) -> Dict[str, Dict[str, Any]]:
        """Get all requests for a conversation"""
        # Use SCAN to find all request keys
        result = {}
        cursor = 0
        while True:
            cursor, keys = await self.redis.scan(
                cursor, match="request_messages:*", count=100
            )
            for key in keys:
                data = await self.redis.get(key)
                if data:
                    parsed = json.loads(data)
                    if parsed.get("conversation_id") == conversation_id:
                        # Handle both bytes (old redis-py) and str (new redis-py)
                        key_str = key.decode() if isinstance(key, bytes) else key
                        request_id = key_str.replace("request_messages:", "")
                        result[request_id] = parsed
            if cursor == 0:
                break
        return result
    
    async def mark_cancelled(self, request_id: str):
        """Mark a request as cancelled"""
        data = await self.get_messages(request_id)
        if data:
            data["status"] = "cancelled"
            data["cancelled_at"] = time.time()
            await self.redis.set(
                f"request_messages:{request_id}",
                json.dumps(data),
                ex=self.ttl
            )
    
    async def delete(self, request_id: str):
        """Delete request tracking data"""
        await self.redis.delete(f"request_messages:{request_id}")
    
    async def cleanup_old_requests(self, conversation_id: int, 
                                  bot_message_ids: List[int]) -> int:
        """
        Clean up old request tracking data.
        Removes requests that have 2+ non-cancelled bot messages after them.
        Returns number of cleaned up requests.
        """
        # Get all requests for this conversation
        all_requests = await self.get_by_conversation(conversation_id)
        if not all_requests:
            return 0
        
        cleaned_count = 0
        
        for request_id, request_data in all_requests.items():
            # Skip if already cancelled or very recent
            if request_data.get("status") == "cancelled":
                continue
                
            request_message_ids = request_data.get("message_ids", [])
            if not request_message_ids:
                continue
            
            # Find the latest user message ID from this request
            latest_user_msg_id = max(request_message_ids)
            
            # Count non-cancelled bot messages after this request's messages
            bot_messages_after = [
                msg_id for msg_id in bot_message_ids 
                if msg_id > latest_user_msg_id
            ]
            
            # If there are 2+ bot messages after this request, it's safe to clean up
            if len(bot_messages_after) >= 2:
                await self.delete(request_id)
                cleaned_count += 1
                logger.debug(f"Cleaned up old request {request_id} (had {len(bot_messages_after)} bot messages after)")
        
        return cleaned_count


class LocalObjectManager:
    """
    Manages objects that must stay local (can't be serialized to Redis).
    These include: WebSocket, asyncio.Task, Anthropic clients, PubSub objects
    """
    
    def __init__(self):
        # These stay local - no Redis
        self.websocket_pubsubs: Dict[str, Any] = {}  # PubSub objects
        self.active_tasks: Dict[str, asyncio.Task] = {}  # Task objects
        self.redis_listener_tasks: Dict[str, asyncio.Task] = {}  # Redis listener tasks
        self.anthropic_clients: Dict[str, Any] = {}  # Client objects
        self.conversation_websockets: Dict[int, List[Tuple[str, WebSocket]]] = {}  # Conversation WebSockets
        self.claude_streams: Dict[str, Any] = {}  # Claude API stream objects for cancellation
        
        # NEW: Reverse lookup for efficient broadcasting
        self.session_conversations: Dict[str, Set[int]] = {}  # session_id -> Set of conversation_ids
        self.session_websocket: Dict[str, WebSocket] = {}  # session_id -> WebSocket (for fast lookup)
        
        # Cache for optimistic ID mappings to avoid database queries
        self.optimistic_to_real: Dict[int, int] = {}  # optimistic_id -> real_id
        self.real_to_optimistic: Dict[int, int] = {}  # real_id -> optimistic_id
        
        # Locks for local objects
        self.pubsub_lock = asyncio.Lock()
        self.tasks_lock = asyncio.Lock()
        self.listener_tasks_lock = asyncio.Lock()
        self.clients_lock = asyncio.Lock()
        self.conversation_websockets_lock = asyncio.Lock()
        self.streams_lock = asyncio.Lock()
        self.session_lookup_lock = asyncio.Lock()  # Lock for reverse lookup structures
    
    # WebSocket PubSub management
    async def add_pubsub(self, session_id: str, pubsub):
        async with self.pubsub_lock:
            # Close old pubsub if exists (handles reconnection with same session ID)
            if session_id in self.websocket_pubsubs:
                old_pubsub = self.websocket_pubsubs[session_id]
                try:
                    await old_pubsub.close()
                    logger.info(f"Closed old pubsub for session {session_id} before replacing")
                except Exception as e:
                    logger.warning(f"Error closing old pubsub for {session_id}: {e}")
            self.websocket_pubsubs[session_id] = pubsub
    
    async def get_pubsub(self, session_id: str):
        async with self.pubsub_lock:
            return self.websocket_pubsubs.get(session_id)
    
    async def remove_pubsub(self, session_id: str):
        async with self.pubsub_lock:
            if session_id in self.websocket_pubsubs:
                pubsub = self.websocket_pubsubs[session_id]
                del self.websocket_pubsubs[session_id]
                return pubsub
        return None
    
    # Task management
    async def add_task(self, request_id: str, task: asyncio.Task):
        async with self.tasks_lock:
            self.active_tasks[request_id] = task
    
    async def get_task(self, request_id: str) -> Optional[asyncio.Task]:
        async with self.tasks_lock:
            return self.active_tasks.get(request_id)
    
    async def remove_task(self, request_id: str):
        async with self.tasks_lock:
            return self.active_tasks.pop(request_id, None)
    
    async def cancel_task(self, request_id: str) -> bool:
        """Cancel a task by request ID"""
        async with self.tasks_lock:
            task = self.active_tasks.get(request_id)
            if task:
                task.cancel()
                del self.active_tasks[request_id]
                return True
            return False
    
    async def get_and_cancel_task(self, request_id: str):
        """Get and cancel a task"""
        async with self.tasks_lock:
            task = self.active_tasks.get(request_id)
            if task:
                task.cancel()
                del self.active_tasks[request_id]
                return task
        return None
    
    async def cancel_tasks_by_ids(self, request_ids: List[str]) -> int:
        """Cancel multiple tasks by their IDs"""
        cancelled_count = 0
        async with self.tasks_lock:
            for request_id in request_ids:
                task = self.active_tasks.get(request_id)
                if task:
                    task.cancel()
                    del self.active_tasks[request_id]
                    cancelled_count += 1
        return cancelled_count
    
    # Anthropic client caching
    async def get_anthropic_client(self, api_key: str):
        async with self.clients_lock:
            return self.anthropic_clients.get(api_key)
    
    async def set_anthropic_client(self, api_key: str, client):
        async with self.clients_lock:
            self.anthropic_clients[api_key] = client
    
    # Redis listener task management
    async def add_listener_task(self, session_id: str, task: asyncio.Task):
        """Add a Redis listener task for a session"""
        async with self.listener_tasks_lock:
            # Cancel old listener task if exists (handles reconnection with same session ID)
            if session_id in self.redis_listener_tasks:
                old_task = self.redis_listener_tasks[session_id]
                old_task.cancel()
                logger.info(f"Cancelled old Redis listener task for session {session_id} before replacing")
                try:
                    await old_task
                except asyncio.CancelledError:
                    pass  # Expected when task is cancelled
                except Exception as e:
                    logger.warning(f"Error while cancelling old listener task for {session_id}: {e}")
            self.redis_listener_tasks[session_id] = task
    
    async def get_listener_task(self, session_id: str) -> Optional[asyncio.Task]:
        """Get the Redis listener task for a session"""
        async with self.listener_tasks_lock:
            return self.redis_listener_tasks.get(session_id)
    
    async def cancel_listener_task(self, session_id: str) -> bool:
        """Cancel and remove the Redis listener task for a session"""
        async with self.listener_tasks_lock:
            task = self.redis_listener_tasks.get(session_id)
            if task:
                task.cancel()
                del self.redis_listener_tasks[session_id]
                logger.info(f"Cancelled Redis listener task for session {session_id}")
                try:
                    await task
                except asyncio.CancelledError:
                    pass  # Expected when task is cancelled
                except Exception as e:
                    logger.warning(f"Error while cancelling listener task for {session_id}: {e}")
                return True
            return False
    
    async def get_all_listener_tasks(self) -> Dict[str, asyncio.Task]:
        """Get all Redis listener tasks (for monitoring/cleanup)"""
        async with self.listener_tasks_lock:
            return dict(self.redis_listener_tasks)
    
    # Conversation WebSocket management
    async def get_websocket_registrations(self, websocket: WebSocket) -> List[int]:
        """Get all conversation IDs where this WebSocket is registered"""
        async with self.conversation_websockets_lock:
            conversation_ids = []
            for conv_id, registrations in self.conversation_websockets.items():
                for sid, ws in registrations:
                    if ws is websocket:  # Check exact WebSocket instance
                        conversation_ids.append(conv_id)
                        break  # Found in this conversation, move to next
            return conversation_ids
    
    async def register_conversation_websocket(self, session_id: str, conversation_id: int, 
                                             websocket: WebSocket, optimistic_id: Optional[int] = None):
        """Register a WebSocket for a conversation, handling migration from optimistic to real IDs"""
        async with self.conversation_websockets_lock:
            async with self.session_lookup_lock:
                # Cache the optimistic->real mapping if provided
                if optimistic_id is not None and conversation_id > 0:
                    self.optimistic_to_real[optimistic_id] = conversation_id
                    self.real_to_optimistic[conversation_id] = optimistic_id
                    logger.info(f"Cached mapping: optimistic {optimistic_id} -> real {conversation_id}")
                
                # If this is a real ID (positive) and this WebSocket is already registered
                # with an optimistic ID (negative), remove the optimistic registration
                if conversation_id > 0:
                    # Check all conversations for this exact WebSocket instance
                    for conv_id in list(self.conversation_websockets.keys()):
                        if conv_id < 0:  # It's an optimistic ID
                            # Check if this WebSocket is registered there
                            registrations = self.conversation_websockets[conv_id]
                            for sid, ws in registrations[:]:  # Use slice copy to allow modification
                                if ws is websocket:  # Same WebSocket instance
                                    registrations.remove((sid, ws))
                                    # Update reverse lookup: remove optimistic ID from session
                                    if sid in self.session_conversations:
                                        self.session_conversations[sid].discard(conv_id)
                                        if not self.session_conversations[sid]:
                                            del self.session_conversations[sid]
                                    # Cache this optimistic->real mapping
                                    self.optimistic_to_real[conv_id] = conversation_id
                                    self.real_to_optimistic[conversation_id] = conv_id
                                    logger.info(f"Removed optimistic registration {conv_id} for WebSocket (session {sid}) migrating to real ID {conversation_id}")
                                    if not registrations:
                                        del self.conversation_websockets[conv_id]
                                    break
                
                # Now proceed with normal registration
                if conversation_id not in self.conversation_websockets:
                    self.conversation_websockets[conversation_id] = []
                
                # Remove any existing entry for this session (handles reconnection)
                existing_entries = [(sid, ws) for sid, ws in self.conversation_websockets[conversation_id] 
                                  if sid == session_id]
                for old_entry in existing_entries:
                    self.conversation_websockets[conversation_id].remove(old_entry)
                    logger.info(f"Replaced old WebSocket registration for session {session_id} in conversation {conversation_id}")
                
                # Add the new WebSocket registration
                self.conversation_websockets[conversation_id].append((session_id, websocket))
                
                # Update reverse lookup mappings
                if session_id not in self.session_conversations:
                    self.session_conversations[session_id] = set()
                self.session_conversations[session_id].add(conversation_id)

                # Update session -> websocket mapping
                self.session_websocket[session_id] = websocket
    
    async def unregister_conversation_websocket(self, session_id: str, conversation_id: Optional[int] = None):
        """Unregister a WebSocket from conversations"""
        async with self.conversation_websockets_lock:
            async with self.session_lookup_lock:
                conversations_cleaned = []
                
                if conversation_id is not None:
                    # Remove from specific conversation
                    if conversation_id in self.conversation_websockets:
                        self.conversation_websockets[conversation_id] = [
                            (sid, ws) for sid, ws in self.conversation_websockets[conversation_id]
                            if sid != session_id
                        ]
                        if not self.conversation_websockets[conversation_id]:
                            del self.conversation_websockets[conversation_id]
                        conversations_cleaned.append(conversation_id)
                        
                        # Update reverse lookup
                        if session_id in self.session_conversations:
                            self.session_conversations[session_id].discard(conversation_id)
                            if not self.session_conversations[session_id]:
                                del self.session_conversations[session_id]
                                # Also remove from session->websocket mapping if no conversations
                                if session_id in self.session_websocket:
                                    del self.session_websocket[session_id]
                else:
                    # Remove from all conversations (cleanup on disconnect)
                    for conv_id in list(self.conversation_websockets.keys()):
                        original_count = len(self.conversation_websockets[conv_id])
                        self.conversation_websockets[conv_id] = [
                            (sid, ws) for sid, ws in self.conversation_websockets[conv_id]
                            if sid != session_id
                        ]
                        if len(self.conversation_websockets[conv_id]) < original_count:
                            conversations_cleaned.append(conv_id)
                        if not self.conversation_websockets[conv_id]:
                            del self.conversation_websockets[conv_id]
                    
                    # Clear all reverse lookup entries for this session
                    if session_id in self.session_conversations:
                        del self.session_conversations[session_id]
                    if session_id in self.session_websocket:
                        del self.session_websocket[session_id]
                
                for conv_id in conversations_cleaned:
                    logger.info(f"Removed WebSocket for session {session_id} from conversation {conv_id}")
    
    async def get_conversation_websockets(self, conversation_id: int) -> List[Tuple[str, WebSocket]]:
        """Get all WebSocket connections for a conversation"""
        async with self.conversation_websockets_lock:
            return list(self.conversation_websockets.get(conversation_id, []))
    
    
    async def get_all_conversation_websockets(self) -> Dict[int, List[Tuple[str, WebSocket]]]:
        """Get all conversation WebSocket mappings (for monitoring/debugging)"""
        async with self.conversation_websockets_lock:
            return dict(self.conversation_websockets)
    
    async def remove_websocket_from_conversation(self, conversation_id: int, session_id: str, 
                                                websocket: WebSocket):
        """Remove a specific WebSocket from a conversation (for cleanup after send errors)"""
        async with self.conversation_websockets_lock:
            if conversation_id in self.conversation_websockets:
                try:
                    self.conversation_websockets[conversation_id].remove((session_id, websocket))
                    if not self.conversation_websockets[conversation_id]:
                        del self.conversation_websockets[conversation_id]
                    return True
                except ValueError:
                    return False
        return False
    
    # Helper methods for reverse lookup
    async def get_session_conversations(self, session_id: str) -> Set[int]:
        """Get all conversation IDs a session is registered for"""
        async with self.session_lookup_lock:
            return set(self.session_conversations.get(session_id, set()))
    
    async def get_session_websocket(self, session_id: str) -> Optional[WebSocket]:
        """Get the WebSocket for a session ID (fast O(1) lookup)"""
        async with self.session_lookup_lock:
            return self.session_websocket.get(session_id)
    
    async def get_all_session_mappings(self) -> Dict[str, Set[int]]:
        """Get all session -> conversations mappings (for debugging/monitoring)"""
        async with self.session_lookup_lock:
            return {sid: set(convs) for sid, convs in self.session_conversations.items()}
    
    async def session_is_registered(self, session_id: str, conversation_id: int) -> bool:
        """Check if a session is registered for a specific conversation"""
        async with self.session_lookup_lock:
            return conversation_id in self.session_conversations.get(session_id, set())
    
    # Optimistic ID cache methods
    async def get_optimistic_id_cached(self, real_id: int) -> Optional[int]:
        """Get cached optimistic ID for a real conversation ID"""
        async with self.session_lookup_lock:
            return self.real_to_optimistic.get(real_id)
    
    async def get_real_id_cached(self, optimistic_id: int) -> Optional[int]:
        """Get cached real ID for an optimistic conversation ID"""
        async with self.session_lookup_lock:
            return self.optimistic_to_real.get(optimistic_id)
    
    async def cache_id_mapping(self, optimistic_id: int, real_id: int):
        """Cache an optimistic->real ID mapping"""
        async with self.session_lookup_lock:
            self.optimistic_to_real[optimistic_id] = real_id
            self.real_to_optimistic[real_id] = optimistic_id
            logger.debug(f"Cached ID mapping: optimistic {optimistic_id} <-> real {real_id}")
    
    async def clear_id_mapping_cache(self, conversation_id: int):
        """Clear cached mappings for a conversation (when it's deleted/completed)"""
        async with self.session_lookup_lock:
            if conversation_id in self.real_to_optimistic:
                opt_id = self.real_to_optimistic[conversation_id]
                del self.real_to_optimistic[conversation_id]
                if opt_id in self.optimistic_to_real:
                    del self.optimistic_to_real[opt_id]
            elif conversation_id in self.optimistic_to_real:
                real_id = self.optimistic_to_real[conversation_id]
                del self.optimistic_to_real[conversation_id]
                if real_id in self.real_to_optimistic:
                    del self.real_to_optimistic[real_id]
    
    # Claude stream management
    async def add_stream(self, request_id: str, stream):
        """Store a Claude API stream for potential cancellation"""
        async with self.streams_lock:
            self.claude_streams[request_id] = stream
            logger.info(f"Stored Claude stream for request {request_id}")
    
    async def get_stream(self, request_id: str):
        """Get a Claude API stream"""
        async with self.streams_lock:
            return self.claude_streams.get(request_id)
    
    async def cancel_stream(self, request_id: str) -> bool:
        """Cancel a Claude API stream"""
        async with self.streams_lock:
            stream = self.claude_streams.get(request_id)
            if stream:
                try:
                    # Anthropic streams have a .close() method for cancellation
                    if hasattr(stream, 'close'):
                        await stream.close()
                    del self.claude_streams[request_id]
                    logger.info(f"Cancelled Claude stream for request {request_id}")
                    return True
                except Exception as e:
                    logger.error(f"Error cancelling stream for request {request_id}: {e}")
                    return False
            return False
    
    async def remove_stream(self, request_id: str):
        """Remove a completed stream from tracking"""
        async with self.streams_lock:
            if request_id in self.claude_streams:
                del self.claude_streams[request_id]
                logger.debug(f"Removed Claude stream for request {request_id}")


# Singleton instances (initialize these in your app startup)
def create_managers(redis_client: redis.Redis) -> Dict[str, Any]:
    """Create all manager instances"""
    return {
        "chat_sessions": ChatSessionManager(redis_client),
        "user_sessions": UserSessionManager(redis_client),
        "session_timestamps": SessionTimestampManager(redis_client),
        "active_requests": ActiveRequestManager(redis_client),
        "session_mappings": SessionMappingManager(redis_client),
        "request_states": RequestStateManager(redis_client),
        "request_messages": RequestMessageTracker(redis_client),
        "local_objects": LocalObjectManager()
    }