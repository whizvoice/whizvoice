"""
Redis managers for WhizVoice - Handles state that needs to be shared across servers
"""
import json
import time
import os
import asyncio
from typing import Dict, List, Set, Optional, Tuple, Any
from fastapi import WebSocket
import redis.asyncio as redis
import logging

logger = logging.getLogger(__name__)

# Server ID for multi-server deployments
SERVER_ID = os.getenv("SERVER_ID", f"server_{os.getpid()}")


class ChatSessionManager:
    """Manages chat message history in Redis"""
    
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.ttl = 900  # 15 minutes
        
    async def get(self, session_id: str) -> List[Dict]:
        """Get chat history for a session"""
        data = await self.redis.get(f"chat_session:{session_id}")
        return json.loads(data) if data else []
    
    async def set(self, session_id: str, messages: List[Dict]):
        """Set entire chat history"""
        await self.redis.set(
            f"chat_session:{session_id}",
            json.dumps(messages),
            ex=self.ttl
        )
    
    async def set_messages(self, session_id: str, messages: List[Dict]):
        """Alias for set() method - for backward compatibility"""
        await self.set(session_id, messages)
    
    async def get_messages(self, session_id: str) -> List[Dict]:
        """Alias for get() method - for backward compatibility"""
        return await self.get(session_id)
    
    async def add_message(self, session_id: str, message: Dict):
        """Alias for append() method - for backward compatibility"""
        await self.append(session_id, message)
    
    async def append(self, session_id: str, message: Dict):
        """Append a message to chat history"""
        # Get existing messages
        messages = await self.get(session_id)
        messages.append(message)
        
        # Trim if too long (keep last 100 messages)
        if len(messages) > 100:
            messages = messages[-100:]
        
        await self.set(session_id, messages)
    
    async def extend(self, session_id: str, new_messages: List[Dict]):
        """Extend chat history with multiple messages"""
        messages = await self.get(session_id)
        messages.extend(new_messages)
        
        # Trim if too long
        if len(messages) > 100:
            messages = messages[-100:]
        
        await self.set(session_id, messages)
    
    async def delete(self, session_id: str):
        """Delete a session"""
        await self.redis.delete(f"chat_session:{session_id}")
    
    async def exists(self, session_id: str) -> bool:
        """Check if session exists"""
        return await self.redis.exists(f"chat_session:{session_id}")


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
                session_id = key.replace("session_timestamp:", "")
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


class ConnectionTracker:
    """
    Tracks WebSocket connections for multi-server support.
    WebSocket objects stay local, but metadata goes to Redis.
    """
    
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        # Local WebSocket storage (can't serialize WebSocket objects)
        self.local_websockets: Dict[str, WebSocket] = {}
        self.local_conversations: Dict[int, List[Tuple[str, WebSocket]]] = {}
        
    async def register(self, session_id: str, user_id: str, 
                      conversation_id: Optional[int], websocket: WebSocket):
        """Register a new WebSocket connection"""
        # Store metadata in Redis
        data = {
            "server_id": SERVER_ID,
            "user_id": user_id,
            "connected_at": time.time()
        }
        if conversation_id is not None:
            data["conversation_id"] = str(conversation_id)
        
        await self.redis.hset(
            f"connection:{session_id}",
            mapping=data
        )
        await self.redis.expire(f"connection:{session_id}", 900)
        
        # Store WebSocket locally
        self.local_websockets[session_id] = websocket
        if conversation_id is not None:
            if conversation_id not in self.local_conversations:
                self.local_conversations[conversation_id] = []
            self.local_conversations[conversation_id].append((session_id, websocket))
    
    async def unregister(self, session_id: str):
        """Unregister a WebSocket connection"""
        # Get metadata first
        data = await self.redis.hgetall(f"connection:{session_id}")
        
        # Remove from Redis
        await self.redis.delete(f"connection:{session_id}")
        
        # Remove from local storage
        if session_id in self.local_websockets:
            del self.local_websockets[session_id]
        
        # Remove from conversation tracking
        if data and "conversation_id" in data:
            conv_id = int(data["conversation_id"])
            if conv_id in self.local_conversations:
                self.local_conversations[conv_id] = [
                    (sid, ws) for sid, ws in self.local_conversations[conv_id]
                    if sid != session_id
                ]
                if not self.local_conversations[conv_id]:
                    del self.local_conversations[conv_id]
    
    async def get_conversation_sessions(self, conversation_id: int) -> List[str]:
        """Get all sessions connected to a conversation (across all servers)"""
        sessions = []
        cursor = 0
        while True:
            cursor, keys = await self.redis.scan(
                cursor, match="connection:*", count=100
            )
            for key in keys:
                data = await self.redis.hget(key, "conversation_id")
                if data and int(data) == conversation_id:
                    session_id = key.replace("connection:", "")
                    sessions.append(session_id)
            if cursor == 0:
                break
        return sessions
    
    def get_local_websockets(self, conversation_id: int) -> List[Tuple[str, WebSocket]]:
        """Get local WebSocket connections for a conversation"""
        return self.local_conversations.get(conversation_id, [])
    
    async def update_conversation(self, session_id: str, old_conv_id: Optional[int], 
                                 new_conv_id: int, websocket: WebSocket):
        """Update conversation ID for a session"""
        # Update Redis
        await self.redis.hset(f"connection:{session_id}", "conversation_id", str(new_conv_id))
        
        # Update local tracking
        if old_conv_id and old_conv_id in self.local_conversations:
            self.local_conversations[old_conv_id] = [
                (sid, ws) for sid, ws in self.local_conversations[old_conv_id]
                if sid != session_id
            ]
            if not self.local_conversations[old_conv_id]:
                del self.local_conversations[old_conv_id]
        
        if new_conv_id not in self.local_conversations:
            self.local_conversations[new_conv_id] = []
        self.local_conversations[new_conv_id].append((session_id, websocket))


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
        
        # Locks for local objects
        self.pubsub_lock = asyncio.Lock()
        self.tasks_lock = asyncio.Lock()
        self.listener_tasks_lock = asyncio.Lock()
        self.clients_lock = asyncio.Lock()
    
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
            logger.info(f"Added Redis listener task for session {session_id}")
    
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
        "connection_tracker": ConnectionTracker(redis_client),
        "local_objects": LocalObjectManager()
    }