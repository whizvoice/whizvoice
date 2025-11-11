from fastapi import FastAPI, WebSocket, HTTPException, WebSocketDisconnect, Depends, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials, HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, ValidationError
from typing import List, Optional, Dict, Any, Union, Set, Tuple
import json
import os
import traceback
import logging
import time
from fastapi.responses import JSONResponse
from datetime import datetime, timedelta
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import redis.asyncio as redis
from redis.asyncio.client import PubSub

from anthropic import AsyncAnthropic, AuthenticationError
from asana_tools import asana_tools, get_asana_tasks, get_asana_workspaces, get_current_date, get_parent_tasks, create_asana_task, change_task_parent, update_task_due_date, delete_asana_task
from about_me_tool import about_me_tools, get_app_info, get_user_data
from screen_agent_tools import screen_agent_tools, launch_app, disable_continuous_listening, set_tts_enabled
from messaging_tools import messaging_tools, whatsapp_select_chat, whatsapp_send_message, whatsapp_draft_message, sms_select_chat, sms_draft_message, sms_send_message
from music_tools import music_tools, play_youtube_music, queue_youtube_music, get_music_app_preference, set_music_app_preference
from maps_tools import maps_tools, search_google_maps_location, search_google_maps_phrase, get_google_maps_directions, recenter_google_maps, select_location_from_list
from color_tools import color_tools, pick_random_color
from location_tools import location_tools, save_location
from weather_tools import weather_tools, get_weather
from tool_result_handler import tool_result_handler
from preferences import set_preference, get_preference, ensure_user_and_prefs, get_decrypted_preference_key, set_encrypted_preference_key, CLAUDE_API_KEY_PREF_NAME, set_user_timezone
from auth import verify_google_token, create_access_token, get_current_user, AuthError, SECRET_KEY as AUTH_SECRET_KEY, ALGORITHM as AUTH_ALGORITHM, create_refresh_token
from supabase_client import supabase
from redis_managers import create_managers
import stripe

try:
    from constants import STRIPE_SECRET_KEY, STRIPE_PRICE_ID
except ImportError:
    # For testing environments where constants.py might not exist
    STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
    STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID", "")
from redis_helpers import (
    # Chat session functions
    get_chat_messages, add_chat_message, set_chat_messages, clear_chat_session,
    # User session functions  
    get_user_sessions, add_user_session, remove_user_session, get_all_user_sessions,
    # Session timestamp functions
    update_session_activity as update_session_activity_redis, 
    get_session_timestamp, remove_session_timestamp,
    get_stale_sessions, get_all_session_timestamps,
    # Active request functions
    add_active_request, remove_active_request, get_active_requests, clear_active_requests,
    # Request state tracking functions
    set_request_state, get_request_state, get_all_request_states,
    # Session mapping functions
    set_session_mapping, get_real_id, get_optimistic_id, clear_session_mappings,
    # Utility functions
    get_total_session_count, get_user_session_count,
    # Module initialization
    set_managers_and_storage
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Stripe
stripe.api_key = STRIPE_SECRET_KEY

# System prompt for Claude
CLAUDE_SYSTEM_PROMPT = """You are Whiz Voice, a friendly AI chatbot that can help with anything. You have access to various tools that you MUST use when appropriate:

1. When the user asks to open/launch an app (like WhatsApp, YouTube, Maps, etc.), you MUST use the 'launch_app' tool
2. For WhatsApp messaging, use the WhatsApp-specific tools (whatsapp_select_chat, whatsapp_draft_message, whatsapp_send_message)
3. For SMS texting, use the SMS-specific tools (sms_select_chat, sms_draft_message, sms_send_message)
4. For Asana/task management, use the Asana tools
5. For app information, use the get_app_info tool
6. For music playback:
   - When the user asks to play music WITHOUT specifying an app, check their music app preference using get_music_app_preference
   - If no preference is set, ask the user which music app they prefer (currently we only support YouTube Music, not Spotify) and save it using set_music_app_preference
   - If the user explicitly specifies an app in their request (e.g., "play on YouTube Music"), use that app and optionally save it as their preference
7. For deciding on a random color when a list of colors isn't specified, ALWAYS use the pick_random_color tool
8. For weather, use the get_weather tool with the appropriate days_ahead parameter (0 = today, 1 = tomorrow, etc.)
9. For saving locations, use the save_location tool - it can save weather_default, home, work, or any named location

IMPORTANT: When a user asks you to open an app, DO NOT just say you opened it - you MUST actually use the launch_app tool to open it on their device. Similarly, use the appropriate tools for all actions rather than just describing what you would do.

Note that you are a voice app, so please keep your responses brief so that they don't take too long to be read out loud.

FORMATTING: You can use markdown formatting in your responses (e.g., **bold**, *italic*, `code`, code blocks with triple backticks, lists, etc.) to improve readability. The app will render markdown appropriately.

DON'T DUPLICATE: You have access to the tool history and the success/failure of past tool calls. If you, for example, sucessfully sent a message or made a task in Asana, DO NOT do the same thing again so the user does not see duplicates.
"""

# can concatenate additional tools here if needed
tools = asana_tools + about_me_tools + screen_agent_tools + messaging_tools + music_tools + maps_tools + color_tools + location_tools + weather_tools

app = FastAPI(
    title="WhizVoice API",
    description="API for WhizVoice chatbot with Asana integration",
    version="1.0.0"
)

# Initialize Redis on app startup
@app.on_event("startup")
async def startup_event():
    await init_redis()
    # Start the background task for cleaning up stale sessions
    asyncio.create_task(cleanup_stale_sessions())
    logger.info(f"Started stale session cleanup task (checking every {CLEANUP_INTERVAL_SECONDS} seconds for sessions older than {SESSION_TIMEOUT_SECONDS} seconds)")
    # Start the background task for cleaning up abandoned tool executions
    asyncio.create_task(cleanup_abandoned_tool_executions())
    logger.info("Started abandoned tool execution cleanup task")

# Clean up on app shutdown
@app.on_event("shutdown")
async def shutdown_event():
    """Clean up resources on app shutdown"""
    logger.info("Starting server shutdown cleanup...")
    
    # Cancel all Redis listener tasks
    if redis_managers and "local_objects" in redis_managers:
        listener_tasks = await redis_managers["local_objects"].get_all_listener_tasks()
        if listener_tasks:
            logger.info(f"Cancelling {len(listener_tasks)} Redis listener tasks...")
            for session_id in list(listener_tasks.keys()):
                await redis_managers["local_objects"].cancel_listener_task(session_id)
    
    # Close Redis connection
    if redis_client:
        await redis_client.close()
        logger.info("Closed Redis connection")
    
    logger.info("Server shutdown cleanup completed")

# Add Request Logging Middleware
@app.middleware("http")
async def log_requests(request: Request, call_next):
    logger.info(f"Incoming request: {request.method} {request.url.path}")
    response = await call_next(request)
    return response

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with your frontend domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Placeholder for the actual preference key name for Claude API key

def get_current_claude_api_key(user_id: Optional[str]) -> Optional[str]:
    if not user_id:
        logger.warning("Attempted to get Claude API key without user_id.")
        return None
    try:
        # Now call get_decrypted_preference_key to ensure we get it from the encrypted source
        key = get_decrypted_preference_key(user_id, CLAUDE_API_KEY_PREF_NAME)
        if key:
            logger.info(f"Successfully retrieved (and decrypted) Claude API key for user {user_id}.")
            return key
        else:
            logger.warning(f"Claude API key not found in preferences for user {user_id} using key_name '{CLAUDE_API_KEY_PREF_NAME}'.")
            return None
    except Exception as e:
        logger.error(f"Error retrieving Claude API key for user {user_id}: {str(e)}")
        return None

# This dictionary will cache Anthropic clients per API key
_anthropic_clients_cache: Dict[str, AsyncAnthropic] = {}

async def get_anthropic_client(user_id: Optional[str]) -> Optional[AsyncAnthropic]:
    api_key = get_current_claude_api_key(user_id)
    if not api_key:
        return None

    async with anthropic_clients_cache_lock:
        if api_key in _anthropic_clients_cache:
            return _anthropic_clients_cache[api_key]
        
        logger.info(f"Creating new AsyncAnthropic client for user {user_id} (key ending with ...{api_key[-4:] if len(api_key) > 4 else ''}).")
        new_client = AsyncAnthropic(api_key=api_key)
        _anthropic_clients_cache[api_key] = new_client
        return new_client

async def call_claude_api(client: AsyncAnthropic, session_id: str, stream: bool = None, conversation_id: Optional[int] = None, with_tools: bool = True):
    """
    Standard method to call Claude API with consistent parameters.

    Always uses stream=False with tools enabled for reliability.
    This ensures tools are always available when needed.

    Returns:
    - Coroutine for complete response (non-streaming)

    conversation_id: Optional - if provided, will reload context from DB if empty
    with_tools: Whether to include tools in the request (default True)
    """
    # Get messages from session
    messages = await get_chat_messages(session_id)

    # SAFETY NET: If context is empty but we have a conversation_id, try to load from database
    # This handles edge cases where Redis session might have been cleared unexpectedly
    if len(messages) == 0 and conversation_id:
        logger.warning(f"[CLAUDE_CONTEXT] Empty context for conversation {conversation_id}, attempting to reload from database")
        try:
            from supabase_client import supabase
            query = supabase.table("messages")\
                .select("id, content, message_sender, timestamp, cancelled, content_type, tool_content")\
                .eq("conversation_id", conversation_id)\
                .order("timestamp", desc=False)

            response = query.execute()
            db_messages = response.data if response.data else []

            redis_messages = []
            # First pass: identify tool_use IDs that have corresponding tool_results
            tool_use_ids_with_results = set()
            for msg in db_messages:
                if msg.get('content_type') == 'tool_result' and msg.get('tool_content'):
                    for block in msg['tool_content']:
                        if isinstance(block, dict) and block.get('tool_use_id'):
                            tool_use_ids_with_results.add(block['tool_use_id'])

            # Second pass: build messages, skipping incomplete tool_use blocks
            for msg in db_messages:
                if msg.get('cancelled'):
                    continue

                content_type = msg.get('content_type', 'text')
                tool_content = msg.get('tool_content')

                # Handle tool_use messages - skip if no corresponding tool_result
                if content_type == 'tool_use' and tool_content:
                    tool_use_id = None
                    for block in tool_content:
                        if isinstance(block, dict) and block.get('id'):
                            tool_use_id = block['id']
                            break

                    if tool_use_id and tool_use_id not in tool_use_ids_with_results:
                        logger.warning(f"Skipping incomplete tool_use (no result): {tool_use_id}")
                        continue

                    redis_messages.append({"role": "assistant", "content": tool_content})
                elif content_type == 'tool_result' and tool_content:
                    redis_messages.append({"role": "user", "content": tool_content})
                # Handle regular text messages
                elif msg['message_sender'] == 'USER':
                    redis_messages.append({"role": "user", "content": msg['content']})
                elif msg['message_sender'] == 'ASSISTANT':
                    redis_messages.append({"role": "assistant", "content": msg['content']})

            if redis_messages:
                await set_chat_messages(session_id, redis_messages)
                messages = redis_messages
                logger.info(f"[CLAUDE_CONTEXT] Reloaded {len(redis_messages)} messages from database")
        except Exception as e:
            logger.error(f"[CLAUDE_CONTEXT] Failed to reload context from database: {e}")

    # Always use non-streaming mode
    stream = False

    # CRITICAL: Merge consecutive messages with same role (Claude API requirement)
    # When merging user messages, tool_result blocks MUST come before text blocks
    merged_messages = []
    for msg in messages:
        if not merged_messages or merged_messages[-1]['role'] != msg['role']:
            # Different role or first message - just append
            merged_messages.append(msg)
        else:
            # Same role as previous - merge content
            prev_msg = merged_messages[-1]
            prev_content = prev_msg['content']
            curr_content = msg['content']

            # Convert both to lists of content blocks
            prev_blocks = prev_content if isinstance(prev_content, list) else [{"type": "text", "text": prev_content}]
            curr_blocks = curr_content if isinstance(curr_content, list) else [{"type": "text", "text": curr_content}]

            # For user messages: tool_result blocks MUST come first, then text blocks
            if msg['role'] == 'user':
                tool_results = [b for b in prev_blocks + curr_blocks if isinstance(b, dict) and b.get('type') == 'tool_result']
                text_blocks = [b for b in prev_blocks + curr_blocks if isinstance(b, dict) and b.get('type') == 'text']
                merged_content = tool_results + text_blocks
            else:
                # For assistant messages: text blocks MUST come first, then tool_use blocks
                text_blocks = [b for b in prev_blocks + curr_blocks if isinstance(b, dict) and b.get('type') == 'text']
                tool_uses = [b for b in prev_blocks + curr_blocks if isinstance(b, dict) and b.get('type') == 'tool_use']
                merged_content = text_blocks + tool_uses

            prev_msg['content'] = merged_content

    messages = merged_messages

    # Log the conversation context being sent to Claude
    logger.info(f"[CLAUDE_CONTEXT] Sending {len(messages)} messages to Claude for session {session_id}, stream={stream}")
    for i, msg in enumerate(messages):
        role = msg.get('role', 'unknown')
        content = msg.get('content', '')
        # Truncate content for logging
        content_preview = content[:100] + "..." if len(content) > 100 else content
        logger.info(f"[CLAUDE_CONTEXT] Message {i}: role={role}, content={content_preview}")

    # Always include tools when requested
    tools_to_send = tools if with_tools else None

    api_params = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1000,
        "messages": messages,
        "system": CLAUDE_SYSTEM_PROMPT,
        "stream": stream
    }

    # Only add tools-related params if we have tools
    if tools_to_send:
        api_params["tools"] = tools_to_send
        api_params["tool_choice"] = {"type": "auto"}
        api_params["betas"] = ["token-efficient-tools-2025-02-19"]

    return client.beta.messages.create(**api_params)

class ChatMessage(BaseModel):
    content: str
    session_id: Optional[str] = None

class ChatResponse(BaseModel):
    content: str
    session_id: str

class GoogleTokenRequest(BaseModel):
    token: str

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str
    user: Dict[str, Any]

class UserApiKeySetRequest(BaseModel):
    key_name: str
    key_value: Optional[str] # Allow None to potentially clear a key

class TokenUpdateRequest(BaseModel):
    claude_api_key: Optional[str] = None
    asana_access_token: Optional[str] = None

class ApiTokenStatusResponse(BaseModel):
    has_claude_token: bool
    has_asana_token: bool

class SetTimezoneRequest(BaseModel):
    timezone: str

# Conversation and Message API models
class ConversationCreate(BaseModel):
    title: str
    source: str = "app"
    google_session_id: Optional[str] = None

class ConversationUpdate(BaseModel):
    title: Optional[str] = None

class ConversationResponse(BaseModel):
    id: int
    user_id: str
    title: str
    created_at: str
    last_message_time: str
    source: str
    google_session_id: Optional[str] = None
    deleted_at: Optional[str] = None

class MessageCreate(BaseModel):
    conversation_id: int
    content: str
    message_type: str  # 'USER' or 'ASSISTANT'
    request_id: Optional[str] = None  # Client-generated UUID for request tracking
    timestamp: Optional[str] = None  # Optional ISO format timestamp for preserving message order

class MessageResponse(BaseModel):
    id: int
    conversation_id: int
    content: str
    message_type: str
    timestamp: str
    request_id: Optional[str] = None  # Request ID for tracking request/response pairs

# Dialogflow webhook models
class DialogflowWebhookRequest(BaseModel):
    detectIntentResponseId: Optional[str] = None
    pageInfo: Optional[Dict[str, Any]] = None
    sessionInfo: Optional[Dict[str, Any]] = None
    fulfillmentInfo: Optional[Dict[str, Any]] = None
    messages: Optional[List[Dict[str, Any]]] = None
    payload: Optional[Dict[str, Any]] = None
    sentimentAnalysisResult: Optional[Dict[str, Any]] = None
    text: Optional[str] = None
    triggerIntent: Optional[str] = None
    triggerEvent: Optional[str] = None
    languageCode: Optional[str] = None

class DialogflowWebhookResponse(BaseModel):
    fulfillmentResponse: Optional[Dict[str, Any]] = None
    pageInfo: Optional[Dict[str, Any]] = None
    sessionInfo: Optional[Dict[str, Any]] = None
    payload: Optional[Dict[str, Any]] = None

# Allow-list of preference keys that can be set via this endpoint
ALLOWED_API_KEY_NAMES = {
    "claude_api_key",
    "asana_access_token",
    # Add other future API key names here
}

# Allow-list of general preference keys that can be set via the user preference endpoint
ALLOWED_PREFERENCE_KEYS = {
    "voice_settings",
    "user_timezone",
    "asana_workspace_preference",
    # Add other preference keys here as needed
}

# Configuration for session management
SESSION_TIMEOUT_SECONDS = 900  # 15 minutes timeout for inactive sessions
CLEANUP_INTERVAL_SECONDS = 300  # Run cleanup every 5 minutes
MAX_SESSIONS_PER_USER = int(os.getenv("MAX_SESSIONS_PER_USER", "5"))  # Max concurrent sessions per user
MAX_TOTAL_SESSIONS = int(os.getenv("MAX_TOTAL_SESSIONS", "500"))  # Max total concurrent sessions
SESSION_WARNING_THRESHOLD = 0.8  # Warn when at 80% capacity

# Redis connection and managers
redis_client: Optional[redis.Redis] = None
redis_managers = None  # Will be initialized after Redis connection

# Helper functions for managing pending tool execution counter
async def increment_pending_tools(conversation_id: int):
    """Increment the pending tool counter for a conversation"""
    if redis_client:
        key = f"conversation:{conversation_id}:pending_tools"
        await redis_client.incr(key)
        await redis_client.expire(key, 60)  # Auto-cleanup after 60s
        logger.debug(f"Incremented pending tools for conversation {conversation_id}")

async def decrement_pending_tools(conversation_id: int):
    """Decrement the pending tool counter for a conversation"""
    if redis_client:
        key = f"conversation:{conversation_id}:pending_tools"
        current = await redis_client.get(key)
        if current and int(current) > 0:
            await redis_client.decr(key)
            logger.debug(f"Decremented pending tools for conversation {conversation_id}")

async def get_pending_tools_count(conversation_id: int) -> int:
    """Get the count of pending tool executions for a conversation"""
    if redis_client:
        key = f"conversation:{conversation_id}:pending_tools"
        count = await redis_client.get(key)
        return int(count) if count else 0
    return 0

async def wait_for_pending_tools(conversation_id: int, timeout_seconds: float = 5.0) -> bool:
    """
    Wait for pending tool executions to complete.
    Returns True if all tools completed, False if timeout reached.
    """
    if not conversation_id:
        return True

    start_time = time.time()
    max_attempts = int(timeout_seconds / 0.5)

    for attempt in range(max_attempts):
        pending_count = await get_pending_tools_count(conversation_id)
        if pending_count == 0:
            logger.info(f"All pending tools completed for conversation {conversation_id}")
            return True

        if time.time() - start_time >= timeout_seconds:
            logger.warning(f"Timeout waiting for {pending_count} pending tools in conversation {conversation_id}")
            return False

        logger.debug(f"Waiting for {pending_count} pending tools (attempt {attempt + 1}/{max_attempts})")
        await asyncio.sleep(0.5)

    return False

# Local-only data structures moved to LocalObjectManager in redis_managers

request_states: Dict[str, Dict[str, Any]] = {}  # Track request states locally as fallback

# Define the response model for the new GET endpoint
ASANA_ACCESS_TOKEN_PREF_NAME = "asana_access_token" # Define this constant

# Locks for thread-safe access to shared dictionaries
chat_sessions_lock = asyncio.Lock()
user_sessions_lock = asyncio.Lock()
session_timestamps_lock = asyncio.Lock()
anthropic_clients_cache_lock = asyncio.Lock()
request_states_lock = asyncio.Lock()




async def init_redis():
    """Initialize Redis connection for pub/sub and session management"""
    global redis_client, redis_managers
    try:
        # Connect to local Redis (default port 6379)
        redis_client = await redis.from_url(
            "redis://localhost:6379",
            encoding="utf-8",
            decode_responses=True
        )
        # Test the connection
        await redis_client.ping()
        logger.info("Successfully connected to Redis for pub/sub")
        
        # Initialize Redis managers for distributed session state
        redis_managers = create_managers(redis_client)
        logger.info("Initialized Redis managers for distributed session management")
        
        # Initialize the helper module with managers and empty local storage (fully on Redis now)
        local_storage = {
            "chat_sessions": {},
            "user_sessions": {},
            "session_timestamps": {},
            "request_states": request_states
        }
        locks = {
            "chat_sessions_lock": chat_sessions_lock,
            "user_sessions_lock": user_sessions_lock,
            "session_timestamps_lock": session_timestamps_lock,
            "request_states_lock": request_states_lock
        }
        set_managers_and_storage(redis_managers, local_storage, locks)
        
    except Exception as e:
        logger.warning(f"Failed to connect to Redis: {str(e)}. WebSocket broadcasting will be limited to single process.")
        redis_client = None
        redis_managers = None


async def subscribe_to_conversation(session_id: str, conversation_id: int, websocket: WebSocket):
    """Subscribe to Redis channel for a conversation"""
    if not redis_client:
        return
    
    try:
        # Create a pubsub instance for this WebSocket
        pubsub = redis_client.pubsub()
        channel_name = f"conversation:{conversation_id}"
        
        # Subscribe to the conversation channel
        await pubsub.subscribe(channel_name)
        
        # Store pubsub in LocalObjectManager
        if redis_managers and "local_objects" in redis_managers:
            await redis_managers["local_objects"].add_pubsub(session_id, pubsub)
        else:
            logger.warning(f"Redis managers not available, pubsub for {session_id} not stored")

        # Start listening for messages in the background and track the task
        listener_task = asyncio.create_task(redis_message_listener(session_id, pubsub, websocket))
        if redis_managers and "local_objects" in redis_managers:
            await redis_managers["local_objects"].add_listener_task(session_id, listener_task)
        else:
            # Fallback if managers not initialized (shouldn't happen in practice)
            logger.warning(f"Redis managers not available, listener task for {session_id} not tracked")
        
    except Exception as e:
        logger.error(f"Failed to subscribe session {session_id} to conversation {conversation_id}: {str(e)}")


async def unsubscribe_from_conversation(session_id: str):
    """Unsubscribe from Redis channels"""
    # Get and remove pubsub from LocalObjectManager
    pubsub = None
    if redis_managers and "local_objects" in redis_managers:
        pubsub = await redis_managers["local_objects"].remove_pubsub(session_id)
    
    if not pubsub:
        return
    
    try:
        await pubsub.unsubscribe()
        await pubsub.close()
        logger.info(f"Session {session_id} unsubscribed from Redis channels")
    except Exception as e:
        logger.error(f"Error unsubscribing session {session_id}: {str(e)}")


async def update_websocket_conversation(session_id: str, old_conversation_id: Optional[int], new_conversation_id: int, websocket: WebSocket):
    """Safely update WebSocket conversation mapping and Redis subscriptions.
    
    This function ensures there's always an active listener during the transition
    by creating the new subscription before destroying the old one.
    """
    if old_conversation_id == new_conversation_id:
        return  # No change needed
    
    logger.info(f"Updating WebSocket conversation for session {session_id}: {old_conversation_id} → {new_conversation_id}")
    
    try:
        # STEP 1: Subscribe to new Redis channel FIRST (before unsubscribing from old)
        await subscribe_to_conversation(session_id, new_conversation_id, websocket)
        
        # STEP 2: Update the conversation_websockets mapping using LocalObjectManager
        if redis_managers and "local_objects" in redis_managers:
            # Pass the old conversation ID as optimistic if it's negative (optimistic)
            optimistic_id = old_conversation_id if old_conversation_id and old_conversation_id < 0 else None
            
            # First unregister from old conversation
            if old_conversation_id:
                await redis_managers["local_objects"].unregister_conversation_websocket(session_id, old_conversation_id)
            
            # Then register with new conversation (with optimistic ID if migrating)
            await redis_managers["local_objects"].register_conversation_websocket(
                session_id, new_conversation_id, websocket, optimistic_id
            )
        
        # STEP 3: NOW unsubscribe from old channel (after new one is active)
        if old_conversation_id:
            # Cancel the old listener task first
            if redis_managers and "local_objects" in redis_managers:
                await redis_managers["local_objects"].cancel_listener_task(session_id)
            await unsubscribe_from_conversation(session_id)
        
        logger.info(f"Successfully updated WebSocket registration for session {session_id}")
        
    except Exception as e:
        logger.error(f"Error updating WebSocket conversation for session {session_id}: {str(e)}")
        # Don't close the WebSocket on error - let it continue with the original setup
        raise


async def redis_message_listener(session_id: str, pubsub: PubSub, websocket: WebSocket):
    """Listen for Redis pub/sub messages and forward to WebSocket"""
    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
                try:
                    # Parse the message data
                    data = json.loads(message["data"])
                    
                    # Don't send to the originating session
                    if data.get("exclude_session") == session_id:
                        continue
                    
                    # Forward the message to this WebSocket
                    # Update activity timestamp before forwarding
                    await update_session_activity_redis(session_id)
                    
                    await websocket.send_text(json.dumps(data["payload"]))
                    logger.info(f"Forwarded Redis message to session {session_id}")
                    
                except json.JSONDecodeError:
                    logger.error(f"Invalid JSON in Redis message: {message['data']}")
                except Exception as e:
                    logger.error(f"Error forwarding message to session {session_id}: {str(e)}")
                    # Don't break on transient forwarding errors - let client retry
                    if "WebSocket" in str(e):
                        break
    except asyncio.CancelledError:
        logger.info(f"Redis listener cancelled for session {session_id}")
        raise  # Re-raise to properly propagate cancellation
    except Exception as e:
        # Check if this is just a connection close during channel switch (expected)
        if "Connection closed by server" in str(e):
            logger.info(f"Redis listener connection closed for session {session_id} (likely during channel switch)")
        else:
            logger.error(f"Redis listener error for session {session_id}: {str(e)}")
            # Only close WebSocket on unexpected Redis failures
            try:
                await websocket.close(code=1011, reason="Redis connection lost")
            except:
                pass


# Tool registry that maps tool names to their configuration
TOOL_REGISTRY = {
    "get_asana_workspaces": {
        "function_name": "get_asana_workspaces",
        "requires_auth": True,
        "args_mapping": lambda args, user_id: (user_id,),
        "validation": None
    },
    "get_asana_tasks": {
        "function_name": "get_asana_tasks", 
        "requires_auth": True,
        "args_mapping": lambda args, user_id: (user_id, args.get('start_date'), args.get('end_date')),
        "validation": None
    },
    "get_current_date": {
        "function_name": "get_current_date",
        "requires_auth": False,
        "args_mapping": lambda args, user_id: (user_id,),
        "validation": None
    },
    "get_parent_tasks": {
        "function_name": "get_parent_tasks",
        "requires_auth": True,
        "args_mapping": lambda args, user_id: (user_id,),
        "validation": None
    },
    "create_asana_task": {
        "function_name": "create_asana_task",
        "requires_auth": True,
        "args_mapping": lambda args, user_id: (
            user_id,
            args.get('name'),
            args.get('due_date'),
            args.get('notes'),
            args.get('parent_task_gid')
        ),
        "validation": lambda args: {"error": "Task name is required."} if not args.get('name') else None
    },
    "set_workspace_preference": {
        "function_name": "set_preference",
        "requires_auth": True,
        "args_mapping": lambda args, user_id: (user_id, 'asana_workspace_preference', args.get('workspace_gid')),
        "validation": lambda args: ValueError("Workspace GID is required for set_workspace_preference") if not args.get('workspace_gid') else None
    },
    "get_workspace_preference": {
        "function_name": "get_preference",
        "requires_auth": True,
        "args_mapping": lambda args, user_id: (user_id, 'asana_workspace_preference'),
        "validation": None  # Will handle user_id check in main flow since it's already covered by requires_auth
    },
    "change_task_parent": {
        "function_name": "change_task_parent",
        "requires_auth": True,
        "args_mapping": lambda args, user_id: (user_id, args.get('task_gid'), args.get('new_parent_gid')),
        "validation": None
    },
    "update_task_due_date": {
        "function_name": "update_task_due_date",
        "requires_auth": True,
        "args_mapping": lambda args, user_id: (user_id, args.get('task_gid'), args.get('new_due_date')),
        "validation": lambda args: (
            {"error": "Task GID is required."} if not args.get('task_gid') else
            {"error": "New due date is required."} if not args.get('new_due_date') else
            None
        )
    },
    "delete_asana_task": {
        "function_name": "delete_asana_task",
        "requires_auth": True,
        "args_mapping": lambda args, user_id: (user_id, args.get('task_gid')),
        "validation": lambda args: {"error": "Task GID is required."} if not args.get('task_gid') else None
    },
    "get_app_info": {
        "function_name": "get_app_info",
        "requires_auth": False,
        "args_mapping": lambda args, user_id: (user_id,),
        "validation": None
    },
    "get_user_data": {
        "function_name": "get_user_data",
        "requires_auth": True,
        "args_mapping": lambda args, user_id: (user_id,),
        "validation": None
    },
    "launch_app": {
        "function_name": "launch_app",
        "requires_auth": False,
        "is_async": True,  # Mark this as an async tool
        "needs_websocket": True,  # This tool needs WebSocket context
        "args_mapping": lambda args, user_id, **kwargs: (
            args.get('app_name'), 
            user_id,
            kwargs.get('websocket'),
            kwargs.get('tool_result_handler'),
            kwargs.get('conversation_id')
        ),
        "validation": lambda args: {"error": "App name is required."} if not args.get('app_name') else None
    },
    "whatsapp_select_chat": {
        "function_name": "whatsapp_select_chat",
        "requires_auth": False,
        "is_async": True,  # Mark this as an async tool
        "needs_websocket": True,  # This tool needs WebSocket context
        "args_mapping": lambda args, user_id, **kwargs: (
            args.get('chat_name'),
            user_id,
            kwargs.get('websocket'),
            kwargs.get('tool_result_handler'),
            kwargs.get('conversation_id')
        ),
        "validation": lambda args: {"error": "Chat name is required."} if not args.get('chat_name') else None
    },
    "whatsapp_send_message": {
        "function_name": "whatsapp_send_message",
        "requires_auth": False,
        "is_async": True,  # Mark this as an async tool
        "needs_websocket": True,  # This tool needs WebSocket context
        "args_mapping": lambda args, user_id, **kwargs: (
            args.get('message'),
            user_id,
            kwargs.get('websocket'),
            kwargs.get('tool_result_handler'),
            kwargs.get('conversation_id')
        ),
        "validation": lambda args: {"error": "Message is required."} if not args.get('message') else None
    },
    "whatsapp_draft_message": {
        "function_name": "whatsapp_draft_message",
        "requires_auth": False,
        "is_async": True,  # Mark this as an async tool
        "needs_websocket": True,  # This tool needs WebSocket context
        "args_mapping": lambda args, user_id, **kwargs: (
            args.get('message'),
            user_id,
            kwargs.get('websocket'),
            kwargs.get('tool_result_handler'),
            kwargs.get('conversation_id'),
            args.get('previous_text')  # Add previous_text parameter
        ),
        "validation": lambda args: {"error": "Message is required."} if not args.get('message') else None
    },
    "sms_select_chat": {
        "function_name": "sms_select_chat",
        "requires_auth": False,
        "is_async": True,
        "needs_websocket": True,
        "args_mapping": lambda args, user_id, **kwargs: (
            args.get('contact_name'),
            user_id,
            kwargs.get('websocket'),
            kwargs.get('tool_result_handler'),
            kwargs.get('conversation_id')
        ),
        "validation": lambda args: {"error": "Contact name is required."} if not args.get('contact_name') else None
    },
    "sms_draft_message": {
        "function_name": "sms_draft_message",
        "requires_auth": False,
        "is_async": True,
        "needs_websocket": True,
        "args_mapping": lambda args, user_id, **kwargs: (
            args.get('message'),
            user_id,
            kwargs.get('websocket'),
            kwargs.get('tool_result_handler'),
            kwargs.get('conversation_id'),
            args.get('previous_text')
        ),
        "validation": lambda args: {"error": "Message is required."} if not args.get('message') else None
    },
    "sms_send_message": {
        "function_name": "sms_send_message",
        "requires_auth": False,
        "is_async": True,
        "needs_websocket": True,
        "args_mapping": lambda args, user_id, **kwargs: (
            args.get('message'),
            user_id,
            kwargs.get('websocket'),
            kwargs.get('tool_result_handler'),
            kwargs.get('conversation_id')
        ),
        "validation": lambda args: {"error": "Message is required."} if not args.get('message') else None
    },
    "disable_continuous_listening": {
        "function_name": "disable_continuous_listening",
        "requires_auth": False,
        "is_async": True,
        "needs_websocket": True,
        "args_mapping": lambda args, user_id, **kwargs: (
            user_id,
            kwargs.get('websocket'),
            kwargs.get('tool_result_handler'),
            kwargs.get('conversation_id')
        ),
        "validation": None
    },
    "set_tts_enabled": {
        "function_name": "set_tts_enabled",
        "requires_auth": False,
        "is_async": True,
        "needs_websocket": True,
        "args_mapping": lambda args, user_id, **kwargs: (
            args.get('enabled'),
            user_id,
            kwargs.get('websocket'),
            kwargs.get('tool_result_handler'),
            kwargs.get('conversation_id')
        ),
        "validation": lambda args: {"error": "enabled parameter is required."} if args.get('enabled') is None else None
    },
    "play_youtube_music": {
        "function_name": "play_youtube_music",
        "requires_auth": False,
        "is_async": True,
        "needs_websocket": True,
        "args_mapping": lambda args, user_id, **kwargs: (
            args.get('query'),
            user_id,
            kwargs.get('websocket'),
            kwargs.get('tool_result_handler'),
            kwargs.get('conversation_id')
        ),
        "validation": lambda args: {"error": "Query is required."} if not args.get('query') else None
    },
    "queue_youtube_music": {
        "function_name": "queue_youtube_music",
        "requires_auth": False,
        "is_async": True,
        "needs_websocket": True,
        "args_mapping": lambda args, user_id, **kwargs: (
            args.get('query'),
            user_id,
            kwargs.get('websocket'),
            kwargs.get('tool_result_handler'),
            kwargs.get('conversation_id')
        ),
        "validation": lambda args: {"error": "Query is required."} if not args.get('query') else None
    },
    "get_music_app_preference": {
        "function_name": "get_music_app_preference",
        "requires_auth": True,
        "args_mapping": lambda args, user_id: (user_id,),
        "validation": None
    },
    "set_music_app_preference": {
        "function_name": "set_music_app_preference",
        "requires_auth": True,
        "args_mapping": lambda args, user_id: (user_id, args.get('music_app')),
        "validation": lambda args: {"error": "music_app parameter is required."} if not args.get('music_app') else None
    },
    "set_user_timezone": {
        "function_name": "set_user_timezone",
        "requires_auth": True,
        "args_mapping": lambda args, user_id: (user_id, args.get('timezone')),
        "validation": lambda args: {"error": "timezone parameter is required."} if not args.get('timezone') else None
    },
    "search_google_maps_location": {
        "function_name": "search_google_maps_location",
        "requires_auth": False,
        "is_async": True,
        "needs_websocket": True,
        "args_mapping": lambda args, user_id, **kwargs: (
            args.get('address_keyword'),
            user_id,
            kwargs.get('websocket'),
            kwargs.get('tool_result_handler'),
            kwargs.get('conversation_id')
        ),
        "validation": lambda args: {"error": "Address keyword is required."} if not args.get('address_keyword') else None
    },
    "search_google_maps_phrase": {
        "function_name": "search_google_maps_phrase",
        "requires_auth": False,
        "is_async": True,
        "needs_websocket": True,
        "args_mapping": lambda args, user_id, **kwargs: (
            args.get('search_phrase'),
            user_id,
            kwargs.get('websocket'),
            kwargs.get('tool_result_handler'),
            kwargs.get('conversation_id')
        ),
        "validation": lambda args: {"error": "Search phrase is required."} if not args.get('search_phrase') else None
    },
    "get_google_maps_directions": {
        "function_name": "get_google_maps_directions",
        "requires_auth": False,
        "is_async": True,
        "needs_websocket": True,
        "args_mapping": lambda args, user_id, **kwargs: (
            args.get('mode'),
            args.get('already_in_directions', False),
            user_id,
            kwargs.get('websocket'),
            kwargs.get('tool_result_handler'),
            kwargs.get('conversation_id')
        ),
        "validation": None
    },
    "recenter_google_maps": {
        "function_name": "recenter_google_maps",
        "requires_auth": False,
        "is_async": True,
        "needs_websocket": True,
        "args_mapping": lambda args, user_id, **kwargs: (
            user_id,
            kwargs.get('websocket'),
            kwargs.get('tool_result_handler'),
            kwargs.get('conversation_id')
        ),
        "validation": None
    },
    "select_location_from_list": {
        "function_name": "select_location_from_list",
        "requires_auth": False,
        "is_async": True,
        "needs_websocket": True,
        "args_mapping": lambda args, user_id, **kwargs: (
            args.get('position'),
            args.get('fragment'),
            user_id,
            kwargs.get('websocket'),
            kwargs.get('tool_result_handler'),
            kwargs.get('conversation_id')
        ),
        "validation": None
    },
    "pick_random_color": {
        "function_name": "pick_random_color",
        "requires_auth": False,
        "args_mapping": lambda args, user_id: (user_id,),
        "validation": None
    },
    "save_location": {
        "function_name": "save_location",
        "requires_auth": True,
        "is_async": True,
        "args_mapping": lambda args, user_id: (
            args.get('location_name'),
            args.get('location_type'),
            user_id
        ),
        "validation": lambda args: (
            {"error": "location_name is required."} if not args.get('location_name') else
            {"error": "location_type is required."} if not args.get('location_type') else
            None
        )
    },
    "get_weather": {
        "function_name": "get_weather",
        "requires_auth": True,
        "is_async": True,
        "args_mapping": lambda args, user_id: (
            args.get('days_ahead', 0),
            user_id,
            args.get('location')  # None if not provided, which will default to 'weather_default'
        ),
        "validation": lambda args: (
            {"error": "days_ahead must be a number."} if args.get('days_ahead') is not None and not isinstance(args.get('days_ahead'), int) else
            None
        )
    }
}

async def execute_tool(tool_name, tool_args, user_id: Optional[str] = None, **context):
    """Execute a tool using the tool registry
    
    Args:
        tool_name: Name of the tool to execute
        tool_args: Arguments for the tool
        user_id: User ID if authenticated
        **context: Additional context (websocket, tool_result_handler, conversation_id, etc.)
    """
    logger.info(f"Executing tool: {tool_name} with args: {tool_args} for user_id: {user_id}")
    
    # Check if tool exists
    if tool_name not in TOOL_REGISTRY:
        logger.error(f"Unknown tool requested: {tool_name}")
        raise ValueError(f"Unknown tool: {tool_name}")
    
    tool_config = TOOL_REGISTRY[tool_name]
    
    # Check authentication requirements
    if tool_config["requires_auth"] and not user_id:
        return {"error": f"User authentication required for tool: {tool_name}"}
    
    # Run validation if present
    if tool_config["validation"]:
        try:
            validation_result = tool_config["validation"](tool_args)
            
            if validation_result:
                if isinstance(validation_result, Exception):
                    logger.error(f"Validation failed for {tool_name}: {str(validation_result)}")
                    raise validation_result
                else:
                    return validation_result  # Return error dict
        except Exception as e:
            logger.error(f"Validation error for {tool_name}: {str(e)}")
            raise e
    
    # Get function arguments using the mapping
    try:
        # Check if this tool needs WebSocket context
        if tool_config.get("needs_websocket", False):
            # Pass additional context to the args mapping
            func_args = tool_config["args_mapping"](tool_args, user_id, **context)
        else:
            # Legacy tools don't need extra context
            func_args = tool_config["args_mapping"](tool_args, user_id)
        
        # Get the actual function using globals() for easy mocking
        function_name = tool_config["function_name"]
        if function_name in globals():
            func = globals()[function_name]
        else:
            raise ValueError(f"Function {function_name} not found")
        
        # Check if this is an async tool
        if tool_config.get("is_async", False):
            # For async tools, await the result
            import asyncio
            if asyncio.iscoroutinefunction(func):
                return await func(*func_args)
            else:
                logger.error(f"Tool {tool_name} marked as async but function is not async")
                raise ValueError(f"Tool {tool_name} misconfigured: marked as async but function is not async")
        else:
            # For sync tools, call normally
            return func(*func_args)
        
    except Exception as e:
        logger.error(f"Error executing tool {tool_name}: {str(e)}")
        raise e

@app.get("/")
async def root():
    return {"message": "Welcome to WhizVoice API"}

@app.post("/auth/google", response_model=TokenResponse)
async def login_with_google(token_request: GoogleTokenRequest):
    try:
        # Verify the Google token
        user_info = verify_google_token(token_request.token)

        # Ensure user and preferences exist
        ensure_user_and_prefs(user_info["sub"], email=user_info["email"])
        
        # Create token data for our service tokens
        # For access token, include more details
        access_token_data = {
            "sub": user_info["sub"],
            "email": user_info["email"],
            "name": user_info["name"]
        }
        # For refresh token, only sub is strictly needed for stateless, but can include email for context
        refresh_token_data = {
            "sub": user_info["sub"]
            # "email": user_info["email"] # Optional: email can be in refresh token for context if desired
        }
        
        access_token = create_access_token(access_token_data)
        refresh_token = create_refresh_token(refresh_token_data)
        
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "user": user_info
        }
    except AuthError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)
    except Exception as e:
        logger.error(f"Error during Google authentication: {str(e)}")
        raise HTTPException(status_code=500, detail="Authentication failed")

class TestAuthRequest(BaseModel):
    email: str
    user_id: str
    name: Optional[str] = "Test User"

def load_test_credentials():
    """Load test credentials from test_credentials.json file"""
    try:
        import json
        with open('test_credentials.json', 'r') as f:
            creds = json.load(f)
            return {
                'username': creds['google_test_account']['email'],
                'password': creds['google_test_account']['password'],
                'allowed_email': creds['google_test_account']['email'],
                'allowed_user_id': creds['google_test_account']['user_id'],
                'allowed_name': creds['google_test_account']['display_name']
            }
    except FileNotFoundError:
        logger.warning("test_credentials.json not found, falling back to environment variables")
        return {
            'username': os.getenv("TEST_AUTH_USERNAME"),
            'password': os.getenv("TEST_AUTH_PASSWORD"),
            'allowed_email': os.getenv("TEST_AUTH_USERNAME"),  # Use username as email fallback
            'allowed_user_id': "test_user_123",
            'allowed_name': "Test User"
        }
    except Exception as e:
        logger.error(f"Error loading test credentials: {e}")
        return None

@app.post("/auth/test", response_model=TokenResponse)
async def login_with_test_credentials(
    test_request: TestAuthRequest,
    credentials: HTTPBasicCredentials = Depends(HTTPBasic())
):
    """
    Test-only authentication endpoint that bypasses Google OAuth.
    Uses HTTP Basic Auth with credentials from test_credentials.json file.
    """
    # Load test credentials from file
    test_creds = load_test_credentials()
    
    if not test_creds or not test_creds['username'] or not test_creds['password']:
        logger.error("Test auth credentials not configured properly")
        raise HTTPException(status_code=503, detail="Test authentication not configured")
    
    # Verify basic auth credentials
    if credentials.username != test_creds['username'] or credentials.password != test_creds['password']:
        logger.warning(f"Invalid test auth credentials attempted from email: {test_request.email}")
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    # Additional security: only allow specific test email and user_id from credentials file
    if test_request.email != test_creds['allowed_email']:
        logger.warning(f"Test auth attempted with invalid email: {test_request.email}")
        raise HTTPException(status_code=401, detail="Invalid test email")
    
    if test_request.user_id != test_creds['allowed_user_id']:
        logger.warning(f"Test auth attempted with invalid user_id: {test_request.user_id}")
        raise HTTPException(status_code=401, detail="Invalid test user_id")
    
    try:
        logger.info(f"Test authentication for: {test_request.email} (user_id: {test_request.user_id})")
        
        # Create user info similar to Google auth
        user_info = {
            "sub": test_request.user_id,
            "email": test_request.email,
            "name": test_request.name,
            "picture": None,  # No profile picture for test users
            "email_verified": True  # Assume test emails are verified
        }

        # 🧪 For test accounts, look up the real Google user ID from Supabase if it exists
        actual_user_id = user_info["sub"]  # Default to the test user ID
        if user_info["email"] == "whizvoicetest@gmail.com":
            try:
                # Look up existing users by email, prioritize non-test user IDs
                existing_users = supabase.table("users").select("user_id").eq("email", user_info["email"]).execute()
                if existing_users.data:
                    # Find the first user that's not the test user ID
                    real_user_id = None
                    for user in existing_users.data:
                        if user["user_id"] != "test_user_123":
                            real_user_id = user["user_id"]
                            break
                    
                    if real_user_id:
                        logger.info(f"🧪 Found real Google user ID for test account: {real_user_id}")
                        actual_user_id = real_user_id
                        # Update user_info to use the real Google user ID
                        user_info["sub"] = actual_user_id
                    else:
                        logger.info(f"🧪 Only test user ID found for {user_info['email']}, using: {actual_user_id}")
                else:
                    logger.info(f"🧪 No existing user found for {user_info['email']}, using test user ID: {actual_user_id}")
            except Exception as e:
                logger.warning(f"🧪 Error looking up real user ID for test account: {e}")
        
        # Ensure user and preferences exist in database
        ensure_user_and_prefs(actual_user_id, email=user_info["email"])
        
        # Create token data (same as Google auth)
        access_token_data = {
            "sub": user_info["sub"],
            "email": user_info["email"],
            "name": user_info["name"]
        }
        refresh_token_data = {
            "sub": user_info["sub"]
        }
        
        access_token = create_access_token(access_token_data)
        refresh_token = create_refresh_token(refresh_token_data)
        
        logger.info(f"Test authentication successful for: {test_request.email}")
        
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "user": user_info
        }
    except Exception as e:
        logger.error(f"Error during test authentication: {str(e)}")
        raise HTTPException(status_code=500, detail="Test authentication failed")

@app.get("/me")
async def get_me(current_user: Dict = Depends(get_current_user)):
    return current_user

@app.get("/preferences/tokens", response_model=ApiTokenStatusResponse)
async def get_api_token_status(current_user: Dict = Depends(get_current_user)):
    user_id = current_user.get("sub")
    if not user_id:
        logger.error("get_api_token_status called without an authenticated user_id.")
        raise HTTPException(status_code=401, detail="User not authenticated")

    try:
        logger.info(f"🔍 [get_api_token_status] Checking tokens for user {user_id}")
        
        # CLAUDE_API_KEY_PREF_NAME should be imported from preferences or defined globally
        claude_key = get_decrypted_preference_key(user_id, CLAUDE_API_KEY_PREF_NAME)
        asana_key = get_decrypted_preference_key(user_id, ASANA_ACCESS_TOKEN_PREF_NAME)
        
        # Log raw values for debugging
        logger.info(f"  Claude key raw check:")
        logger.info(f"    Type: {type(claude_key)}, Is None: {claude_key is None}")
        logger.info(f"    Repr: {repr(claude_key)}")
        if claude_key is not None:
            logger.info(f"    Length: {len(claude_key)}, Empty string: {claude_key == ''}")

        # Updated logic to handle both None and string "None"
        has_claude = bool(claude_key) and claude_key != "None"
        has_asana = bool(asana_key) and asana_key != "None"
        
        logger.info(f"  Results using updated logic (bool check + not 'None'):")
        logger.info(f"    has_claude_token: {has_claude}, has_asana_token: {has_asana}")
        
        return ApiTokenStatusResponse(
            has_claude_token=has_claude,
            has_asana_token=has_asana
        )
    except Exception as e:
        logger.error(f"Error checking token status for user {user_id}: {str(e)}")
        return ApiTokenStatusResponse(
            has_claude_token=False,
            has_asana_token=False
        )

@app.post("/user/api_key", status_code=200) # Singular, updates one key at a time
async def set_user_api_key(
    request: Request,
    current_user: Dict = Depends(get_current_user) # Ensures endpoint is protected
):
    user_id = current_user.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    # Log the raw request body to debug 422 errors from Android app
    try:
        body = await request.body()
        logger.info(f"📱 /user/api_key request from user {user_id}")
        logger.info(f"  Raw body bytes: {body}")
        logger.info(f"  Body length: {len(body)} bytes")
        
        body_str = body.decode('utf-8')
        logger.info(f"  Decoded body string: '{body_str}'")
        
        # Parse the body as JSON
        body_json = json.loads(body_str)
        logger.info(f"  Parsed JSON: {body_json}")
        logger.info(f"  JSON keys: {list(body_json.keys())}")
        
        # Validate against our expected model
        api_request = UserApiKeySetRequest(**body_json)
        logger.info(f"  ✅ Successfully validated request: key_name='{api_request.key_name}', key_value={'[REDACTED]' if api_request.key_value else 'None/empty'}")
        
    except json.JSONDecodeError as e:
        logger.error(f"  ❌ JSON decode error: {e}")
        logger.error(f"  Body was: '{body_str}'")
        raise HTTPException(status_code=422, detail=f"Invalid JSON: {str(e)}")
    except ValidationError as e:
        logger.error(f"  ❌ Pydantic validation error: {e}")
        logger.error(f"  Expected fields: key_name (str), key_value (Optional[str])")
        logger.error(f"  Received: {body_json}")
        raise HTTPException(status_code=422, detail=f"Validation error: {str(e.errors())}")
    except Exception as e:
        logger.error(f"  ❌ Unexpected error parsing request: {type(e).__name__}: {e}")
        raise HTTPException(status_code=422, detail=f"Error parsing request: {str(e)}")
    
    # Use the validated request object from here
    request = api_request
    
    if request.key_name not in ALLOWED_API_KEY_NAMES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid key_name: '{request.key_name}'. Allowed keys are: {list(ALLOWED_API_KEY_NAMES)}"
        )
    
    # Check if this is a CLEAR operation (None or empty string)
    if request.key_value is None or request.key_value == "":
        # Use the new atomic clear-and-verify RPC function
        logger.info(f"🗑️ Clearing key '{request.key_name}' using atomic RPC for user {user_id}")
        
        from preferences import clear_and_verify_encrypted_token
        result = clear_and_verify_encrypted_token(user_id, request.key_name)
        
        if result and result.get('success') and result.get('token_cleared'):
            logger.info(f"✅ Successfully cleared '{request.key_name}' for user {user_id}")
            logger.info(f"  Clear result: {result}")
            
            # Return the updated token status immediately
            # get_decrypted_preference_key is already imported at the top
            claude_token = get_decrypted_preference_key(user_id, 'claude_api_key')
            asana_token = get_decrypted_preference_key(user_id, 'asana_access_token')
            
            return {
                "message": f"Successfully cleared API key: '{request.key_name}'",
                "cleared": True,
                "has_claude_token": bool(claude_token),
                "has_asana_token": bool(asana_token)
            }
        else:
            logger.error(f"Failed to clear '{request.key_name}' for user {user_id}. Result: {result}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to clear API key: '{request.key_name}'"
            )
    else:
        # Normal SET operation for non-null values
        logger.info(f"🔑 Setting key '{request.key_name}' for user {user_id}")
        logger.info(f"  Value type: {type(request.key_value)}")
        logger.info(f"  Value repr: {repr(request.key_value)}")
        
        if set_encrypted_preference_key(user_id, request.key_name, request.key_value):
            logger.info(f"✅ Successfully set preference key '{request.key_name}' for user {user_id}.")
            
            # Immediately check what was stored
            retrieved_value = get_decrypted_preference_key(user_id, request.key_name)
            logger.info(f"🔍 Verification - Retrieved value after setting:")
            logger.info(f"  Retrieved type: {type(retrieved_value)}")
            logger.info(f"  Retrieved is None: {retrieved_value is None}")
            logger.info(f"  Retrieved repr: {repr(retrieved_value)}")
            logger.info(f"  Retrieved length: {len(retrieved_value) if retrieved_value is not None else 'N/A'}")
            logger.info(f"  Bool evaluation: {bool(retrieved_value)}")
            
            # Return the updated token status immediately
            # get_decrypted_preference_key is already imported at the top
            claude_token = get_decrypted_preference_key(user_id, 'claude_api_key')
            asana_token = get_decrypted_preference_key(user_id, 'asana_access_token')
            
            return {
                "message": f"Successfully set API key: '{request.key_name}'",
                "has_claude_token": bool(claude_token),
                "has_asana_token": bool(asana_token)
            }
        else:
            logger.error(f"Failed to set preference key '{request.key_name}' for user {user_id}.")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to set API key: '{request.key_name}'"
            )

class RefreshTokenRequest(BaseModel):
    refresh_token: str

class NewAccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"

@app.post("/auth/refresh", response_model=NewAccessTokenResponse)
async def refresh_access_token(request_data: RefreshTokenRequest):
    try:
        from jose import jwt, JWTError # Ensure JWTError is available

        logger.info(f"Refresh token attempt. Token (first 15): {request_data.refresh_token[:15]}...")
        
        payload = jwt.decode(
            request_data.refresh_token,
            AUTH_SECRET_KEY, 
            algorithms=[AUTH_ALGORITHM],
            options={"verify_aud": False} # Assuming refresh tokens don't have specific audience
        )
        
        token_type = payload.get("type")
        if token_type != "refresh":
            logger.warning(f"Invalid token type for refresh: '{token_type}'")
            raise HTTPException(status_code=401, detail="Invalid token type for refresh")

        user_id = payload.get("sub")
        if not user_id:
            logger.warning("Refresh token payload missing 'sub' (user_id).")
            raise HTTPException(status_code=401, detail="Invalid refresh token: user_id missing")

        # For stateless refresh, we assume if it decodes and is type 'refresh', it's valid.
        # If we had a revocation list or stored refresh tokens, we'd check that here.

        # Fetch user details from database to include in the new access token
        # This ensures the refreshed token has all the necessary fields (email, name, etc.)
        # that subscription and other endpoints might require
        try:
            user_data = supabase.table("users").select("email, user_id").eq("user_id", user_id).execute()
            if not user_data.data or len(user_data.data) == 0:
                logger.error(f"User {user_id} not found in database during token refresh")
                raise HTTPException(status_code=401, detail="User not found")

            user_email = user_data.data[0].get("email")

            # Create a new access token with complete user information
            new_access_token_data = {
                "sub": user_id,
                "email": user_email,
                # Include name if it was in the original token (check what /auth/google includes)
            }
            logger.info(f"Creating refreshed access token for user {user_id} with email {user_email}")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error fetching user data during token refresh: {str(e)}")
            raise HTTPException(status_code=500, detail="Failed to fetch user data")

        new_access_token = create_access_token(new_access_token_data)
        
        logger.info(f"Successfully refreshed access token for user {user_id}.")
        return NewAccessTokenResponse(access_token=new_access_token)

    except JWTError as e:
        logger.warning(f"JWTError during refresh token validation: {str(e)}")
        raise HTTPException(status_code=401, detail=f"Invalid or expired refresh token: {str(e)}")
    except HTTPException as e: # Re-raise HTTPExceptions to ensure they propagate correctly
        raise e
    except Exception as e:
        logger.error(f"Unexpected error during token refresh: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail="Could not refresh token due to server error")

# Stripe Subscription Models
class CreateCheckoutSessionRequest(BaseModel):
    success_url: str
    cancel_url: str

class CreateCheckoutSessionResponse(BaseModel):
    checkout_url: str
    session_id: str

class CancelSubscriptionResponse(BaseModel):
    status: str
    message: str
    canceled_at: Optional[int] = None

class SubscriptionStatusResponse(BaseModel):
    has_subscription: bool
    subscription_id: Optional[str] = None
    status: Optional[str] = None
    current_period_end: Optional[int] = None
    cancel_at_period_end: Optional[bool] = None

# Stripe Subscription Endpoints
@app.post("/subscription/create-checkout-session", response_model=CreateCheckoutSessionResponse)
async def create_checkout_session(
    request_data: CreateCheckoutSessionRequest,
    current_user: dict = Depends(get_current_user)
):
    """Create a Stripe checkout session for subscription"""
    try:
        user_id = current_user.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="User ID not found")
        
        # Create Stripe customer if doesn't exist
        customers = stripe.Customer.list(email=current_user.get("email"), limit=1)
        if customers.data:
            customer = customers.data[0]
        else:
            customer = stripe.Customer.create(
                email=current_user.get("email"),
                metadata={"user_id": user_id}
            )
        
        # Create checkout session
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price': STRIPE_PRICE_ID,
                'quantity': 1,
            }],
            mode='subscription',
            success_url=request_data.success_url,
            cancel_url=request_data.cancel_url,
            customer=customer.id,
            metadata={"user_id": user_id}
        )
        
        return CreateCheckoutSessionResponse(
            checkout_url=checkout_session.url,
            session_id=checkout_session.id
        )
        
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error creating checkout session: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating checkout session: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to create checkout session")

@app.get("/subscription/status", response_model=SubscriptionStatusResponse)
async def get_subscription_status(current_user: dict = Depends(get_current_user)):
    """Get current subscription status for the user"""
    try:
        user_id = current_user.get("sub")
        email = current_user.get("email")
        
        if not user_id or not email:
            raise HTTPException(status_code=401, detail="User information incomplete")
        
        # Find customer by email
        customers = stripe.Customer.list(email=email, limit=1)
        if not customers.data:
            return SubscriptionStatusResponse(has_subscription=False)
        
        customer = customers.data[0]
        
        # Get active subscriptions
        subscriptions = stripe.Subscription.list(
            customer=customer.id,
            status='active',
            limit=1
        )
        
        if not subscriptions.data:
            # Check for canceled subscriptions that are still active until period end
            subscriptions = stripe.Subscription.list(
                customer=customer.id,
                status='all',
                limit=1
            )
            if subscriptions.data and subscriptions.data[0].status in ['active', 'trialing']:
                subscription = subscriptions.data[0]
                return SubscriptionStatusResponse(
                    has_subscription=True,
                    subscription_id=subscription.id,
                    status=subscription.status,
                    current_period_end=subscription.current_period_end,
                    cancel_at_period_end=subscription.cancel_at_period_end
                )
            return SubscriptionStatusResponse(has_subscription=False)
        
        subscription = subscriptions.data[0]
        return SubscriptionStatusResponse(
            has_subscription=True,
            subscription_id=subscription.id,
            status=subscription.status,
            current_period_end=subscription.current_period_end,
            cancel_at_period_end=subscription.cancel_at_period_end
        )
        
    except Exception as e:
        logger.error(f"Error getting subscription status: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to get subscription status")

@app.post("/subscription/cancel", response_model=CancelSubscriptionResponse)
async def cancel_subscription(current_user: dict = Depends(get_current_user)):
    """Cancel the user's subscription at period end"""
    try:
        user_id = current_user.get("sub")
        email = current_user.get("email")
        
        if not user_id or not email:
            raise HTTPException(status_code=401, detail="User information incomplete")
        
        # Find customer by email
        customers = stripe.Customer.list(email=email, limit=1)
        if not customers.data:
            raise HTTPException(status_code=404, detail="No subscription found")
        
        customer = customers.data[0]
        
        # Get active subscriptions
        subscriptions = stripe.Subscription.list(
            customer=customer.id,
            status='active',
            limit=1
        )
        
        if not subscriptions.data:
            raise HTTPException(status_code=404, detail="No active subscription found")
        
        # Cancel subscription at period end
        subscription = stripe.Subscription.modify(
            subscriptions.data[0].id,
            cancel_at_period_end=True
        )
        
        return CancelSubscriptionResponse(
            status="success",
            message="Subscription will be canceled at the end of the billing period",
            canceled_at=subscription.current_period_end
        )
        
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error canceling subscription: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error canceling subscription: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to cancel subscription")

@app.websocket("/chat")
async def websocket_endpoint(websocket: WebSocket):
    # Extract authentication token from query parameters or headers
    try:
        # Accept the connection first
        await websocket.accept()
        
        # Try to get auth token from query parameters or headers
        token = None
        
        if "authorization" in websocket.headers:
            auth_header = websocket.headers.get("authorization")
            if auth_header and auth_header.startswith("Bearer "):
                token = auth_header.replace("Bearer ", "")
        
        if not token and "token" in websocket.query_params:
            token = websocket.query_params["token"]
        
        # Get conversation_id from query parameters if provided
        conversation_id = None
        if "conversation_id" in websocket.query_params:
            try:
                conversation_id = int(websocket.query_params["conversation_id"])
                logger.info(f"WebSocket connection requested for conversation_id={conversation_id}")
            except ValueError:
                logger.warning(f"Invalid conversation_id parameter: {websocket.query_params['conversation_id']}")
        
        # Authenticate if token is present
        user_id = None
        resources_allocated = False  # Track if we've allocated resources that need cleanup
        if token:
            try:
                # Verify token using our server's algorithm (HS256)
                from jose import jwt, JWTError

                payload = jwt.decode(token, AUTH_SECRET_KEY, algorithms=[AUTH_ALGORITHM])
                user_id = payload.get("sub")
                user_email = payload.get("email")
                user_name = payload.get("name", "there")
                
                logger.info(f"Authenticated WebSocket connection for user {user_email} ({user_id})")
                
                # Check global session limit before creating new session
                total_sessions = await get_total_session_count()
                
                # Reject if at capacity
                if total_sessions >= MAX_TOTAL_SESSIONS:
                    logger.error(f"MAX_TOTAL_SESSIONS reached: {total_sessions}/{MAX_TOTAL_SESSIONS}. Rejecting connection from {user_email}")
                    
                    # Clean up any resources that may have been allocated before capacity check
                    # Note: At this point, session_id hasn't been created yet, so no cleanup needed
                    
                    error_payload = {
                        "type": "error",
                        "code": "SERVICE_AT_CAPACITY",
                        "message": "Service at capacity. Please try again later."
                    }
                    await websocket.send_text(json.dumps(error_payload))
                    await websocket.close(code=1013, reason="Service at capacity")  # 1013: Try Again Later
                    return
                
                # Warn if approaching capacity
                warning_threshold = int(MAX_TOTAL_SESSIONS * SESSION_WARNING_THRESHOLD)
                if total_sessions >= warning_threshold:
                    capacity_percent = (total_sessions / MAX_TOTAL_SESSIONS) * 100
                    logger.warning(f"Session count high: {total_sessions}/{MAX_TOTAL_SESSIONS} ({capacity_percent:.1f}% capacity)")
                
                # CRITICAL FIX: Resolve optimistic conversation IDs to real IDs before setting up the session
                # This ensures we subscribe to the correct Redis channel and track the correct conversation
                actual_conversation_id = conversation_id
                if conversation_id is not None and conversation_id < 0:
                    # This is an optimistic ID, check if it has a real ID in the database
                    logger.info(f"Checking if optimistic conversation ID {conversation_id} has been migrated to a real ID")
                    opt_result = supabase.table("conversations")\
                        .select("id")\
                        .eq("user_id", user_id)\
                        .eq("optimistic_chat_id", str(conversation_id))\
                        .is_("deleted_at", "null")\
                        .execute()
                    
                    if opt_result.data and len(opt_result.data) > 0:
                        actual_conversation_id = opt_result.data[0]["id"]
                        logger.info(f"Optimistic ID {conversation_id} has been migrated to real ID {actual_conversation_id}, will use real ID for session")
                    else:
                        logger.info(f"Optimistic ID {conversation_id} has not been migrated yet, will use optimistic ID")
                
                # Load conversation history and initialize session
                # Create a unique session ID per conversation, not just per user
                # Note: We keep the original conversation_id in the session_id for consistency,
                # but use actual_conversation_id for all operations
                if conversation_id is not None:
                    session_id = f"ws_{user_id}_conv_{conversation_id}"
                else:
                    # If no specific conversation, create a session for a new conversation
                    session_id = f"ws_{user_id}_new_{int(time.time())}"
                
                # IMPORTANT: Track session immediately to ensure cleanup even if errors occur
                await update_session_activity_redis(session_id)
                resources_allocated = True  # Mark that we've started allocating resources
                
                # Use actual_conversation_id for loading history (it already handles optimistic IDs internally,
                # but we've already resolved it so we can pass the real ID directly)
                conversation_history = load_conversation_history(user_id, actual_conversation_id)

                await set_chat_messages(session_id, conversation_history)

                # Track the actual conversation_id for this session (not the optimistic one)
                session_conversation_id = actual_conversation_id
                
                # Register WebSocket with the actual conversation for broadcasting
                if actual_conversation_id is not None:
                    await register_websocket_for_conversation(session_id, actual_conversation_id, websocket)
                    
                    # Subscribe to Redis channel for the actual conversation (not the optimistic ID)
                    await subscribe_to_conversation(session_id, actual_conversation_id, websocket)
                
                # FIXED: Don't automatically load existing conversation when conversation_id is None
                # This allows new chats to create fresh conversations instead of reusing old ones
                if actual_conversation_id is None:
                    # For new chats, keep session_conversation_id as None until first message creates it
                    logger.info(f"New chat session - will create conversation on first message")
                
                # 🔧 CRITICAL FIX: Don't modify session_id after creation
                # The session_id should remain consistent throughout the WebSocket connection
                # The original session_id is already correctly formatted based on conversation_id

                # Log the resolution if it happened
                if conversation_id != actual_conversation_id:
                    logger.info(f"WebSocket connected with optimistic ID {conversation_id}, resolved to real ID {actual_conversation_id}")
                    
            except JWTError as e:
                logger.warning(f"WebSocket JWTError: {str(e)}. Closing connection.")
                
                # Clean up any resources that may have been allocated
                if resources_allocated and 'session_id' in locals():
                    logger.info(f"Cleaning up resources for failed auth session {session_id}")
                    
                    # Clean up session timestamp if it was set
                    await remove_session_timestamp(session_id)
                    
                    # Clean up chat messages if they were loaded
                    await clear_chat_session(session_id)
                    
                    # Clean up any Redis subscriptions
                    await unsubscribe_from_conversation(session_id)
                    
                    # Clean up session mappings
                    await clear_session_mappings(session_id)
                    
                    # Clean up conversation websockets
                    if redis_managers and "local_objects" in redis_managers:
                        await redis_managers["local_objects"].unregister_conversation_websocket(session_id)
                    
                    # Clean up from user sessions if it was added
                    if 'user_id' in locals() and user_id:
                        await remove_user_session(user_id, session_id)
                
                error_payload = {
                    "type": "error",
                    "code": "AUTH_JWT_INVALID",
                    "message": "Authentication failed. Please login again."
                }
                await websocket.send_text(json.dumps(error_payload))
                await websocket.close(code=1008, reason="Invalid token") # 1008: Policy Violation
                return 
            except Exception as e:
                logger.error(f"Error during WebSocket authentication: {str(e)}")
                logger.error(traceback.format_exc())
                
                # Clean up any resources that may have been allocated
                if resources_allocated and 'session_id' in locals():
                    logger.info(f"Cleaning up resources for failed auth session {session_id}")
                    
                    # Clean up session timestamp if it was set
                    await remove_session_timestamp(session_id)
                    
                    # Clean up chat messages if they were loaded
                    await clear_chat_session(session_id)
                    
                    # Clean up any Redis subscriptions
                    await unsubscribe_from_conversation(session_id)
                    
                    # Clean up session mappings
                    await clear_session_mappings(session_id)
                    
                    # Clean up conversation websockets
                    if redis_managers and "local_objects" in redis_managers:
                        await redis_managers["local_objects"].unregister_conversation_websocket(session_id)
                    
                    # Clean up from user sessions if it was added
                    if 'user_id' in locals() and user_id:
                        await remove_user_session(user_id, session_id)
                
                error_payload = {
                    "type": "error",
                    "code": "AUTH_GENERAL_ERROR",
                    "message": "Authentication failed. Please contact support."
                }
                await websocket.send_text(json.dumps(error_payload))
                await websocket.close(code=1011)
                return
        else:
            # No anonymous connections allowed
            logger.warning("Unauthenticated WebSocket connection attempt")
            await websocket.send_text("Authentication required. Please login.")
            await websocket.close(code=1008, reason="Authentication required")
            return
        
        # Create a session ID (moved this up since we need it earlier)
        # Associate session with user
        await add_user_session(user_id, session_id)
        
        # Check if user has exceeded session limit and evict old sessions if needed
        # IMPORTANT: Must happen AFTER adding new session to get accurate count
        await evict_user_sessions_if_needed(user_id, session_id)
        
        try:
            while True:
                try:
                    # Receive message from client
                    message_text = await websocket.receive_text()
                    
                    # Update session timestamp on activity
                    # Update activity timestamp for receiving a message
                    await update_session_activity_redis(session_id)
                    logger.debug(f"Updated activity timestamp for session {session_id} (received message)")
                    
                    # Parse incoming message - support both structured JSON and legacy plain text
                    request_id = None
                    message_type = "message"  # default type
                    client_conversation_id = None
                    client_message_id = None
                    client_timestamp = None
                    try:
                        message_data = json.loads(message_text)
                        message = message_data.get("message", "")
                        request_id = message_data.get("request_id")
                        message_type = message_data.get("type", "message")  # Support message types
                        
                        # Get conversation_id from message (if provided)
                        message_conversation_id = message_data.get("conversation_id")
                        client_conversation_id = message_data.get("client_conversation_id")
                        client_message_id = message_data.get("client_message_id")
                        # Get timestamp from client for preserving message order
                        client_timestamp = message_data.get("timestamp")
                        
                        # If message includes a conversation_id, update the session's conversation_id only if it changed
                        if message_conversation_id is not None and message_conversation_id > 0:
                            if session_conversation_id != message_conversation_id:
                                logger.info(f"Updating session conversation_id from {session_conversation_id} to {message_conversation_id}")
                                session_conversation_id = message_conversation_id
                            # else: conversation_id matches, no update needed
                        
                        logger.info(f"Received structured message with request_id: {request_id}, type: {message_type}, conversation_id: {message_conversation_id}, client_conversation_id: {client_conversation_id}")
                        
                        # Validate client_conversation_id immediately
                        # Convert to int if it's a string number, and check if positive
                        if client_conversation_id is not None:
                            try:
                                client_conv_id_int = int(client_conversation_id) if isinstance(client_conversation_id, str) else client_conversation_id
                                if client_conv_id_int > 0:
                                    error_msg = f"Invalid client_conversation_id: {client_conversation_id}. Client conversation IDs must be negative (optimistic) values. Use the conversation_id URL parameter for server-assigned IDs."
                                    logger.error(error_msg)
                                    await websocket.send_json({
                                        "error": error_msg,
                                        "type": "error",
                                        "request_id": request_id
                                    })
                                    continue  # Skip processing this message
                                # Update client_conversation_id to be the integer version for consistency
                                client_conversation_id = client_conv_id_int
                            except (ValueError, TypeError):
                                # If it's not a valid number, log and continue with original value
                                logger.warning(f"client_conversation_id is not a valid number: {client_conversation_id} (type: {type(client_conversation_id)})")
                        
                        # If session_conversation_id is None (new conversation) and we have an optimistic ID, use it
                        if session_conversation_id is None and client_conversation_id is not None and client_conversation_id < 0:
                            logger.info(f"Setting session_conversation_id to optimistic ID {client_conversation_id} for new conversation")
                            session_conversation_id = client_conversation_id
                            # Register and subscribe to this optimistic conversation ID
                            # This ensures the WebSocket can receive broadcasts immediately
                            await register_websocket_for_conversation(session_id, client_conversation_id, websocket)
                            await subscribe_to_conversation(session_id, client_conversation_id, websocket)
                    except json.JSONDecodeError:
                        # Fallback for legacy plain text messages
                        message = message_text
                        logger.info("Received legacy plain text message")
                    
                    # Handle tool result messages from Android
                    if message_type == "tool_result":
                        tool_request_id = message_data.get("request_id")
                        tool_result = message_data.get("result", {})
                        
                        logger.info(f"Received tool_result for request_id: {tool_request_id}")
                        logger.info(f"Tool result content: {json.dumps(tool_result)}")
                        
                        # Pass the result to the handler which will complete the waiting Future
                        if tool_request_id:
                            success = tool_result_handler.handle_tool_result(tool_request_id, tool_result)
                            if success:
                                logger.info(f"Successfully delivered tool result for request {tool_request_id}")
                            else:
                                logger.warning(f"No pending execution found for tool result {tool_request_id}")
                        else:
                            logger.warning("Received tool_result without request_id")
                        
                        continue  # Don't process as a regular message
                    
                    # Handle cancellation requests
                    if message_type == "cancel":
                        cancel_request_id = message_data.get("cancel_request_id")
                        if cancel_request_id and redis_managers and "local_objects" in redis_managers:
                            task = await redis_managers["local_objects"].get_and_cancel_task(cancel_request_id)
                            if task:
                                logger.info(f"Cancelling request {cancel_request_id}")
                        
                        # Remove from active requests tracking in Redis
                        if redis_managers and "active_requests" in redis_managers:
                            await redis_managers["active_requests"].remove(session_id, cancel_request_id)
                        
                        # Send cancellation confirmation
                        cancel_response = {
                            "type": "cancelled",
                            "cancelled_request_id": cancel_request_id,
                            "request_id": request_id
                        }
                        await websocket.send_text(json.dumps(cancel_response))
                        continue
                    
                    # Handle regular messages
                    if message_type == "message" and message:
                        # Check for active requests and handle interrupts
                        # IMPORTANT: Don't treat rapid-fire messages as interrupts (e.g., offline queue)
                        # Only cancel if there's significant time between messages (>2 seconds)
                        has_active_requests = False
                        active_request_ids = []
                        should_interrupt = False
                        
                        if redis_managers and "active_requests" in redis_managers:
                            active_request_ids = list(await redis_managers["active_requests"].get_all(session_id))
                            has_active_requests = len(active_request_ids) > 0
                            # Note: We don't clear active requests here anymore since we're only cancelling streams
                            # The tasks will remove themselves when they complete or fail
                            
                            # Check if this is a true interrupt (user sending new message after waiting)
                            # vs rapid messages (offline queue, copy-paste, etc)
                            if has_active_requests and client_timestamp:
                                # Get timestamp of last active request to compare
                                # For now, we'll be conservative and only interrupt if explicitly requested
                                # This prevents offline message queues from cancelling each other
                                should_interrupt = message_data.get("interrupt_previous", False)
                                if should_interrupt:
                                    logger.info("Client explicitly requested interrupt of previous messages")
                        
                        # Only handle interrupts if explicitly requested or clear user intent
                        if has_active_requests and should_interrupt:
                            # Validate interrupt context if optimistic ID provided
                            if client_conversation_id and redis_managers and "session_mappings" in redis_managers:
                                real_id = await redis_managers["session_mappings"].get_real_id(
                                    session_id, client_conversation_id
                                )
                                
                                if real_id and real_id != session_conversation_id:
                                    logger.warning(f"Interrupt attempt with mismatched conversation context: "
                                                 f"client={client_conversation_id}, session={session_conversation_id}")
                                    
                                logger.info(f"Validated interrupt: optimistic {client_conversation_id} → real {real_id}")
                            
                            logger.info(f"Interrupt detected. Cancelling {len(active_request_ids)} active requests")
                            # Try to cancel Claude streams first, if that fails, cancel the task
                            # The task will keep running until it checks for cancellation
                            stream_cancelled_count = 0
                            task_cancelled_count = 0
                            if redis_managers and "local_objects" in redis_managers:
                                for req_id in active_request_ids:
                                    # First try to cancel the stream (if Claude has been called)
                                    if await redis_managers["local_objects"].cancel_stream(req_id):
                                        stream_cancelled_count += 1
                                        logger.debug(f"Cancelled Claude stream for request {req_id}")
                                    else:
                                        # No stream yet, cancel the task instead
                                        # The task will continue until it checks cancellation status
                                        if await redis_managers["local_objects"].cancel_task(req_id):
                                            task_cancelled_count += 1
                                            logger.debug(f"Marked task for cancellation: {req_id}")
                                logger.info(f"Cancelled {stream_cancelled_count} Claude streams and marked {task_cancelled_count} tasks for cancellation")
                            
                            # Send interrupt notification with client context
                            interrupt_response = {
                                "type": "interrupted", 
                                "message": "Previous request cancelled due to new message",
                                "request_id": request_id,
                                "client_conversation_id": client_conversation_id
                            }
                            await websocket.send_text(json.dumps(interrupt_response))
                        
                        # Before creating the new task, detect and cancel subset requests
                        # We'll get the message IDs after the message is saved in process_message_task
                        # But we need to do detection after the task starts to get the correct message IDs
                        # So we'll move the detection into process_message_task itself
                        
                        # Define a callback to handle task completion
                        async def handle_task_completion(task_future):
                            try:
                                updated_session_conversation_id = await task_future
                                if updated_session_conversation_id is not None:
                                    # Register WebSocket with new conversation ID if it changed
                                    if updated_session_conversation_id != session_conversation_id:
                                        # Use the safe update function that creates new listener before destroying old one
                                        try:
                                            await update_websocket_conversation(session_id, session_conversation_id, updated_session_conversation_id, websocket)
                                        except Exception as e:
                                            logger.error(f"Failed to update WebSocket conversation in task completion: {str(e)}")
                                        
                                        # Note: We can't update session_conversation_id here as it's in the outer scope
                                        # This might need additional handling if conversation ID changes are critical
                                        logger.info(f"Conversation ID changed from {session_conversation_id} to {updated_session_conversation_id}")
                            except asyncio.CancelledError:
                                # This shouldn't happen anymore since we only cancel streams, not tasks
                                logger.warning(f"Unexpected task cancellation for request {request_id}")
                                # Clean up tracking
                                if request_id and redis_managers:
                                    if "local_objects" in redis_managers:
                                        await redis_managers["local_objects"].remove_task(request_id)
                                    if "active_requests" in redis_managers:
                                        await redis_managers["active_requests"].remove(session_id, request_id)
                            except Exception as e:
                                logger.error(f"Error in task completion handler: {e}")
                                # Clean up tracking
                                if request_id and redis_managers:
                                    if "local_objects" in redis_managers:
                                        await redis_managers["local_objects"].remove_task(request_id)
                                    if "active_requests" in redis_managers:
                                        await redis_managers["active_requests"].remove(session_id, request_id)
                        
                        # Create task for processing this message
                        task = asyncio.create_task(
                            process_message_task(
                                websocket=websocket,
                                session_id=session_id,
                                session_conversation_id=session_conversation_id,
                                user_id=user_id,
                                message=message,
                                request_id=request_id,
                                client_conversation_id=client_conversation_id,
                                client_message_id=client_message_id,
                                client_timestamp=client_timestamp
                            )
                        )
                        
                        # Track the task
                        if request_id and redis_managers:
                            if "local_objects" in redis_managers:
                                await redis_managers["local_objects"].add_task(request_id, task)
                            if "active_requests" in redis_managers:
                                await redis_managers["active_requests"].add(session_id, request_id)
                        
                        # Create a separate task to handle completion without blocking
                        # This allows the WebSocket loop to continue receiving messages
                        asyncio.create_task(handle_task_completion(task))
                        
                        logger.info(f"Started background processing for request {request_id}, continuing to listen for messages")

                except AuthenticationError as claude_auth_exc: # MODIFIED: Use AuthenticationError directly
                    logger.warning(f"Anthropic authentication error for user {user_id} in session {session_id}: {claude_auth_exc}")
                    error_payload = {
                        "type": "error",
                        "code": "CLAUDE_AUTHENTICATION_ERROR",
                        "message": f"Claude API authentication failed: {str(claude_auth_exc)}. Please check your Claude API Key in settings.",
                        "request_id": request_id
                    }
                    await websocket.send_text(json.dumps(error_payload))
                    # Don't set _is_responding to False here, as the loop will continue or break
                    # Let the client handle this message and decide if it should resend or wait.
                    # Consider if the connection should be kept open or closed based on auth failure.
                    # For now, keeping it open to allow the user to fix the key.
                    continue # Continue to the next iteration of the loop to receive next message
                except StopIteration as si:
                    if str(si) == "AsanaAuthErrorHandled" or str(si) == "ToolBlockMissingError":
                        logger.info(f"Stopped iteration due to: {str(si)}. Awaiting next message.")
                        continue # Go to the top of the `while True` to await next user message
                    else:
                        logger.warning(f"Unhandled StopIteration: {str(si)}. Re-raising.")
                        raise # Re-raise other StopIterations if any
                except WebSocketDisconnect:
                    logger.info(f"WebSocket disconnected for session {session_id}, allowing active tasks to complete")
                    # Wait for any active tasks to complete (with timeout)
                    active_reqs = await get_active_requests(session_id)
                    if active_reqs:
                        logger.info(f"Waiting for {len(active_reqs)} active requests to complete for session {session_id}")
                        # Give tasks up to 65 seconds to complete (5 seconds more than API timeout)
                        wait_start = time.time()
                        max_wait = 65.0
                        
                        while time.time() - wait_start < max_wait:
                            active_reqs = await get_active_requests(session_id)
                            if not active_reqs:
                                logger.info(f"All tasks completed for session {session_id}")
                                break
                            await asyncio.sleep(0.5)
                        
                        if active_reqs:
                            logger.warning(f"Timed out waiting for {len(active_reqs)} tasks for session {session_id}")
                    
                    await cleanup_session(session_id, user_id, session_conversation_id)
                    logger.info(f"WebSocket cleanup completed for session {session_id}")
                    break
                except Exception as e:
                    # Check if this is a WebSocket disconnection error FIRST
                    if "WebSocket is not connected" in str(e) or "close message has been sent" in str(e) or "Need to call" in str(e):
                        logger.info(f"WebSocket connection lost for session {session_id}, allowing active tasks to complete")
                        # Wait for any active tasks to complete (with timeout)
                        active_reqs = await get_active_requests(session_id)
                        if active_reqs:
                            logger.info(f"Waiting for {len(active_reqs)} active requests to complete for session {session_id}")
                            # Give tasks up to 65 seconds to complete (5 seconds more than API timeout)
                            wait_start = time.time()
                            max_wait = 65.0
                            
                            while time.time() - wait_start < max_wait:
                                active_reqs = await get_active_requests(session_id)
                                if not active_reqs:
                                    logger.info(f"All tasks completed for session {session_id}")
                                    break
                                await asyncio.sleep(0.5)
                            
                            if active_reqs:
                                logger.warning(f"Timed out waiting for {len(active_reqs)} tasks for session {session_id}")
                        
                        await cleanup_session(session_id, user_id, session_conversation_id)
                        logger.info(f"WebSocket cleanup completed for session {session_id}")
                        break
                    
                    # For other errors, log and try to send error to client
                    logger.error(f"Error during WebSocket message processing for session {session_id}: {str(e)}", exc_info=True)
                    logger.error(traceback.format_exc())
                    try:
                        # Send a structured JSON error to the client
                        error_payload = {
                            "type": "error", 
                            "code": "SERVER_PROCESSING_ERROR", 
                            "message": f"An error occurred: {str(e)}",
                            "request_id": request_id
                        }
                        await websocket.send_text(json.dumps(error_payload))
                    except Exception as send_exc:
                        logger.error(f"Failed to send error to client for session {session_id}: {str(send_exc)}")
                    continue # Only continue for recoverable errors
                    
        except Exception as e:
            # Handle any other errors that might occur
            await cleanup_session(session_id, user_id, session_conversation_id)
            logger.error(f"WebSocket error: {str(e)}")
            logger.error(traceback.format_exc())
            raise
    except Exception as e:
        logger.error(f"Error accepting WebSocket connection: {str(e)}")
        logger.error(traceback.format_exc())
        
        # Try to send error message if connection was already accepted
        try:
            await websocket.send_text(f"Error: {str(e)}")
            await websocket.close(code=1011)
        except:
            pass

async def register_websocket_for_conversation(session_id: str, conversation_id: int, websocket: WebSocket):
    """Register a WebSocket for a conversation, handling both real and optimistic IDs"""
    if redis_managers and "local_objects" in redis_managers:
        await redis_managers["local_objects"].register_conversation_websocket(
            session_id, conversation_id, websocket
        )


async def broadcast_to_conversation_parallel(conversation_id: int, message_payload: dict, exclude_session: Optional[str] = None):
    """
    Broadcast a message to all WebSocket connections for a specific conversation.
    
    This version uses parallel sending with asyncio.gather() but still iterates through
    connections list. Kept for reference/fallback. The main broadcast_to_conversation
    function now uses the fully optimized reverse-lookup approach.
    
    Performance improvement: ~10-100x faster than sequential sending for 10-100 connections.
    """
    
    # First, try Redis pub/sub for cross-process broadcasting
    if redis_client:
        try:
            # Publish to the real conversation ID channel
            channel_name = f"conversation:{conversation_id}"
            message_data = {
                "payload": message_payload,
                "exclude_session": exclude_session
            }
            await redis_client.publish(channel_name, json.dumps(message_data))
            logger.info(f"Published message to Redis channel {channel_name} (excluding session: {exclude_session})")
            
            # Check for optimistic ID - first try cache, then database
            optimistic_id = None
            if redis_managers and "local_objects" in redis_managers:
                optimistic_id = await redis_managers["local_objects"].get_optimistic_id_cached(conversation_id)
            
            if not optimistic_id:
                # No cached mapping - query database
                try:
                    opt_result = supabase.table("conversations")\
                        .select("optimistic_chat_id")\
                        .eq("id", conversation_id)\
                        .execute()
                    
                    if opt_result.data and opt_result.data[0].get("optimistic_chat_id"):
                        optimistic_id = opt_result.data[0]["optimistic_chat_id"]
                        
                        # Cache this mapping for future use
                        if redis_managers and "local_objects" in redis_managers:
                            await redis_managers["local_objects"].cache_id_mapping(optimistic_id, conversation_id)
                except Exception as opt_e:
                    logger.warning(f"Could not check for optimistic ID for conversation {conversation_id}: {str(opt_e)}")
            
            if optimistic_id:
                opt_channel_name = f"conversation:{optimistic_id}"
                await redis_client.publish(opt_channel_name, json.dumps(message_data))
                logger.info(f"Also published message to optimistic Redis channel {opt_channel_name}")
        except Exception as e:
            logger.error(f"Failed to publish to Redis: {str(e)}")
    
    # Also use local broadcasting for WebSockets in this process
    connections = []
    
    if redis_managers and "local_objects" in redis_managers:
        local_objects = redis_managers["local_objects"]
        
        # Get connections for the real conversation ID
        connections.extend(await local_objects.get_conversation_websockets(conversation_id))
        
        # Check for optimistic ID using cached mapping first (avoids database query)
        optimistic_id = await local_objects.get_optimistic_id_cached(conversation_id)
        
        if optimistic_id:
            # Found cached optimistic ID - use it directly
            opt_connections = await local_objects.get_conversation_websockets(optimistic_id)
            if opt_connections:
                logger.info(f"Found WebSockets registered under cached optimistic ID {optimistic_id} for real conversation {conversation_id}")
                connections.extend(opt_connections)
        else:
            # No cached mapping - fall back to database query (and cache the result)
            try:
                opt_result = supabase.table("conversations")\
                    .select("optimistic_chat_id")\
                    .eq("id", conversation_id)\
                    .execute()
                
                if opt_result.data and opt_result.data[0].get("optimistic_chat_id"):
                    optimistic_id = int(opt_result.data[0]["optimistic_chat_id"])
                    
                    # Cache this mapping for future use
                    await local_objects.cache_id_mapping(optimistic_id, conversation_id)
                    
                    opt_connections = await local_objects.get_conversation_websockets(optimistic_id)
                    if opt_connections:
                        logger.info(f"Found WebSockets registered under optimistic ID {optimistic_id} for real conversation {conversation_id} (cached for future use)")
                        connections.extend(opt_connections)
            except Exception as e:
                logger.warning(f"Could not check for optimistic ID in database: {str(e)}")
    
    if not connections:
        logger.info(f"No local WebSocket sessions registered for conversation {conversation_id} (checked real and optimistic IDs)")
        return
    
    # Remove duplicates and exclude the originating session
    unique_connections = {}
    for session_id, websocket in connections:
        if exclude_session and session_id == exclude_session:
            logger.info(f"Skipping originating session {session_id}")
            continue
        unique_connections[session_id] = websocket
    
    if not unique_connections:
        logger.info(f"No sessions to broadcast to after filtering")
        return
    
    logger.info(f"Broadcasting locally to conversation {conversation_id} with {len(unique_connections)} registered sessions: {list(unique_connections.keys())}")
    
    # Pre-serialize the message once instead of per connection
    message_json = json.dumps(message_payload)
    
    # Helper function to send to a single session
    async def send_to_session(session_id: str, websocket):
        """Send message to a single session, return (session_id, websocket) if failed"""
        try:
            # Update activity for session receiving broadcast
            await update_session_activity_redis(session_id)
            await websocket.send_text(message_json)  # Use pre-serialized JSON
            logger.info(f"Broadcasted message locally to session {session_id} for conversation {conversation_id}")
            return None  # Success
        except Exception as e:
            logger.warning(f"Failed to broadcast locally to session {session_id}: {str(e)}")
            return (session_id, websocket)  # Return failed connection info
    
    # Send to all connections IN PARALLEL using asyncio.gather
    results = await asyncio.gather(*[
        send_to_session(sid, ws)
        for sid, ws in unique_connections.items()
    ], return_exceptions=False)
    
    # Collect disconnected sessions from results
    disconnected_sessions = [result for result in results if result is not None]
    
    # Clean up disconnected sessions
    if disconnected_sessions and redis_managers and "local_objects" in redis_managers:
        # Clean up in parallel as well
        cleanup_tasks = [
            redis_managers["local_objects"].remove_websocket_from_conversation(
                conversation_id, session_id, websocket
            )
            for session_id, websocket in disconnected_sessions
        ]
        await asyncio.gather(*cleanup_tasks, return_exceptions=True)


async def broadcast_to_conversation(conversation_id: int, message_payload: dict, exclude_session: Optional[str] = None):
    """
    Highly optimized broadcast using reverse lookups with batched operations.
    
    This is the main broadcast function, fully optimized with:
    - Reverse lookup for O(1) WebSocket retrieval
    - Parallel sending using asyncio.gather()
    - Batched session activity updates
    - Efficient cleanup of failed connections
    
    Performance: ~10-100x faster than sequential sending for typical scenarios.
    """
    
    # First, try Redis pub/sub for cross-process broadcasting (same as before)
    if redis_client:
        try:
            channel_name = f"conversation:{conversation_id}"
            message_data = {
                "payload": message_payload,
                "exclude_session": exclude_session
            }
            
            # Get optimistic ID from cache if available
            optimistic_id = None
            if redis_managers and "local_objects" in redis_managers:
                optimistic_id = await redis_managers["local_objects"].get_optimistic_id_cached(conversation_id)
            
            # Publish to both channels in parallel if we have optimistic ID
            publish_tasks = [redis_client.publish(channel_name, json.dumps(message_data))]
            if optimistic_id:
                opt_channel_name = f"conversation:{optimistic_id}"
                publish_tasks.append(redis_client.publish(opt_channel_name, json.dumps(message_data)))
            
            await asyncio.gather(*publish_tasks, return_exceptions=True)
            logger.info(f"Published to Redis channels for conversation {conversation_id}")
        except Exception as e:
            logger.error(f"Failed to publish to Redis: {str(e)}")
    
    # Optimized local broadcasting using reverse lookups
    if not redis_managers or "local_objects" not in redis_managers:
        return
    
    local_objects = redis_managers["local_objects"]
    
    # Get all session mappings to find target sessions efficiently
    all_session_mappings = await local_objects.get_all_session_mappings()
    
    # Find all sessions subscribed to this conversation (including optimistic IDs)
    target_sessions = set()
    
    # Check for real conversation ID
    for sid, convs in all_session_mappings.items():
        if conversation_id in convs and sid != exclude_session:
            target_sessions.add(sid)
    
    # Check for optimistic ID
    optimistic_id = await local_objects.get_optimistic_id_cached(conversation_id)
    if optimistic_id:
        for sid, convs in all_session_mappings.items():
            if optimistic_id in convs and sid != exclude_session:
                target_sessions.add(sid)
    
    if not target_sessions:
        logger.info(f"No local sessions to broadcast to for conversation {conversation_id}")
        return
    
    logger.info(f"Broadcasting to {len(target_sessions)} sessions for conversation {conversation_id}")
    
    # Pre-serialize message once
    message_json = json.dumps(message_payload)
    
    # Batch update all session activities in parallel FIRST
    activity_tasks = [update_session_activity_redis(sid) for sid in target_sessions]
    await asyncio.gather(*activity_tasks, return_exceptions=True)
    
    # Get WebSockets using reverse lookup and send in parallel
    async def send_to_session_fast(session_id: str):
        """Optimized send that uses reverse lookup for WebSocket retrieval"""
        try:
            ws = await local_objects.get_session_websocket(session_id)
            if ws:
                await ws.send_text(message_json)
                return None
            else:
                logger.warning(f"No WebSocket found for session {session_id}")
                return session_id
        except Exception as e:
            logger.warning(f"Failed to send to {session_id}: {str(e)}")
            return session_id
    
    # Send to all sessions in parallel
    results = await asyncio.gather(*[
        send_to_session_fast(sid) for sid in target_sessions
    ], return_exceptions=False)
    
    # Clean up failed sessions
    failed_sessions = [sid for sid in results if sid is not None]
    if failed_sessions:
        cleanup_tasks = [
            local_objects.unregister_conversation_websocket(sid, conversation_id)
            for sid in failed_sessions
        ]
        await asyncio.gather(*cleanup_tasks, return_exceptions=True)
        logger.info(f"Cleaned up {len(failed_sessions)} disconnected sessions")


async def cleanup_session(session_id: str, user_id: Optional[str] = None, conversation_id: Optional[int] = None):
    """Clean up a session when a WebSocket disconnects"""
    # Don't delete chat history immediately - let any active message processing tasks complete
    # They need the session history to generate responses
    # The session will be cleaned up when the task completes or after a timeout
    logger.info(f"Cleaning up session {session_id}, but keeping chat history for active tasks")
    
    # Clean up session timestamp
    await remove_session_timestamp(session_id)
    logger.info(f"Cleaned up session timestamp: {session_id}")
    
    # Cancel Redis listener task first (before closing pubsub)
    if redis_managers and "local_objects" in redis_managers:
        cancelled = await redis_managers["local_objects"].cancel_listener_task(session_id)
        if cancelled:
            logger.info(f"Cancelled Redis listener task for session {session_id}")
    
    # Clean up Redis subscriptions (after cancelling the listener)
    await unsubscribe_from_conversation(session_id)
    
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
        # Group consecutive messages by role ONLY (not request_id) to ensure proper alternation
        # This is critical because Claude API requires strict user/assistant alternation
        claude_messages = []
        current_group = None  # (role, content_blocks)

        for row in result.data:
            message_role = "user" if row["message_sender"] == "USER" else "assistant"

            # Determine if this row belongs to current group (same role)
            should_group = (
                current_group is not None and
                message_role == current_group[0]
            )

            if should_group:
                # Add to current group
                if row.get("tool_content"):
                    current_group[1].extend(row["tool_content"])
                elif row.get("content") and row["content"].strip():
                    current_group[1].append({"type": "text", "text": row["content"]})
            else:
                # Flush current group if exists
                if current_group and current_group[1]:
                    claude_messages.append({
                        "role": current_group[0],
                        "content": current_group[1]
                    })

                # Start new group
                content_blocks = []
                if row.get("tool_content"):
                    content_blocks.extend(row["tool_content"])
                elif row.get("content") and row["content"].strip():
                    content_blocks.append({"type": "text", "text": row["content"]})

                current_group = [message_role, content_blocks]

        # Flush final group
        if current_group and current_group[1]:
            claude_messages.append({
                "role": current_group[0],
                "content": current_group[1]
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

async def detect_and_cancel_subset_requests(conversation_id: int, new_message_ids: List[int], websocket=None, session_id=None) -> List[str]:
    """
    Detect requests that are subsets of the new message set and cancel them.
    Returns list of cancelled request IDs.
    """
    cancelled_requests = []
    cancelled_bot_messages = []  # Track (bot_message_id, request_id) tuples for delete notifications

    if not redis_managers or "request_messages" not in redis_managers:
        return cancelled_requests

    try:
        # Get all requests for this conversation
        all_requests = await redis_managers["request_messages"].get_by_conversation(conversation_id)

        for request_id, request_data in all_requests.items():
            # Skip if already cancelled
            if request_data.get("status") == "cancelled":
                continue

            old_message_ids = set(request_data.get("message_ids", []))
            new_message_ids_set = set(new_message_ids)

            # Check if old request is a subset of new request
            if old_message_ids and old_message_ids.issubset(new_message_ids_set) and old_message_ids != new_message_ids_set:
                logger.info(f"Request {request_id} (messages {old_message_ids}) is subset of new request (messages {new_message_ids_set})")

                # Check if this request has already sent a bot response
                # Query the database for bot messages with this request_id
                try:
                    bot_msg_result = supabase.table("messages")\
                        .select("id")\
                        .eq("request_id", request_id)\
                        .eq("message_sender", "ASSISTANT")\
                        .is_("cancelled", "null")\
                        .execute()

                    if bot_msg_result.data:
                        for bot_msg in bot_msg_result.data:
                            bot_message_id = bot_msg["id"]
                            cancelled_bot_messages.append((bot_message_id, request_id))  # Store both ID and request_id

                            # Mark the bot message as cancelled in the database
                            supabase.table("messages")\
                                .update({"cancelled": "now()"})\
                                .eq("id", bot_message_id)\
                                .execute()
                            logger.info(f"Marked bot message {bot_message_id} as cancelled for request {request_id}")
                except Exception as e:
                    logger.error(f"Error checking/cancelling bot messages for request {request_id}: {e}")
                
                # Cancel the Claude stream if it exists
                if redis_managers and "local_objects" in redis_managers:
                    stream_cancelled = await redis_managers["local_objects"].cancel_stream(request_id)
                    if stream_cancelled:
                        logger.info(f"Cancelled Claude stream for request {request_id}")
                
                # Mark request as cancelled in tracking
                await redis_managers["request_messages"].mark_cancelled(request_id)
                cancelled_requests.append(request_id)
                
                # Cancel only the Claude stream, not the entire task
                # This preserves the message in Redis while stopping Claude generation
                if redis_managers and "local_objects" in redis_managers:
                    stream_cancelled = await redis_managers["local_objects"].cancel_stream(request_id)
                    if stream_cancelled:
                        logger.info(f"Cancelled Claude stream for subset request {request_id}")
        
        if cancelled_requests:
            logger.info(f"Cancelled {len(cancelled_requests)} subset requests: {cancelled_requests}")
        
        # Send delete notifications for cancelled bot messages
        if cancelled_bot_messages and websocket and session_id:
            for bot_message_id, request_id in cancelled_bot_messages:
                delete_notification = {
                    "type": "delete_message",
                    "message_id": bot_message_id,
                    "conversation_id": conversation_id,
                    "request_id": request_id,  # Include request_id so client can match and delete local message
                    "reason": "superseded_by_new_request"
                }
                try:
                    await websocket.send_text(json.dumps(delete_notification))
                    logger.info(f"Sent delete notification for bot message {bot_message_id} (request {request_id}) to session {session_id}")
                except Exception as e:
                    logger.warning(f"Failed to send delete notification for message {bot_message_id}: {e}")
            
    except Exception as e:
        logger.error(f"Error detecting subset requests: {e}")
    
    return cancelled_requests

def save_message_to_db(user_id: str, conversation_id: Optional[int], content: str, message_sender: str, request_id: Optional[str] = None, client_conversation_id: Optional[int] = None, client_timestamp: Optional[str] = None, content_type: str = "text", tool_content: Optional[dict] = None) -> Optional[Tuple[int, int]]:
    """Save a message to the database and return (conversation_id, message_id)

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

        # For ASSISTANT messages with request_id, mark any previous ASSISTANT messages with the same request_id as cancelled
        # This handles the case where streaming responses create multiple intermediate messages
        if message_sender == "ASSISTANT" and request_id:
            try:
                # Find all previous ASSISTANT messages with this request_id that aren't already cancelled
                previous_messages = supabase.table("messages")\
                    .select("id")\
                    .eq("conversation_id", conversation_id)\
                    .eq("request_id", request_id)\
                    .eq("message_sender", "ASSISTANT")\
                    .is_("cancelled", "null")\
                    .execute()

                if previous_messages.data:
                    # Mark all previous messages as cancelled
                    message_ids = [msg["id"] for msg in previous_messages.data]
                    logger.info(f"Marking {len(message_ids)} previous ASSISTANT message(s) as cancelled for request_id={request_id}: {message_ids}")

                    for msg_id in message_ids:
                        supabase.table("messages")\
                            .update({"cancelled": "now()"})\
                            .eq("id", msg_id)\
                            .execute()

                    logger.info(f"Successfully marked {len(message_ids)} previous ASSISTANT message(s) as cancelled")
            except Exception as e:
                logger.error(f"Error marking previous ASSISTANT messages as cancelled for request_id={request_id}: {e}")

        # Prepare message data
        message_data = {
            "conversation_id": conversation_id,
            "content": content,
            "message_sender": message_sender,
            "content_type": content_type,
            "request_id": request_id
        }

        # Add tool_content if provided
        if tool_content is not None:
            message_data["tool_content"] = tool_content
            logger.info(f"Including tool_content in message: {json.dumps(tool_content)[:100]}...")

        # For USER messages with client_timestamp, use the provided timestamp to preserve message order
        if message_sender == "USER" and client_timestamp:
            # Client timestamp is already in ISO format from Android client
            message_data["timestamp"] = client_timestamp
            logger.info(f"Using client-provided timestamp for USER message: {client_timestamp}")
        
        # For ASSISTANT messages with request_id, set timestamp to be right after the USER message
        # This ensures the response appears immediately after the user message it's responding to
        if message_sender == "ASSISTANT" and request_id:
            # Find the USER message with this request_id
            user_msg_result = supabase.table("messages")\
                .select("timestamp")\
                .eq("conversation_id", conversation_id)\
                .eq("request_id", request_id)\
                .eq("message_sender", "USER")\
                .execute()
            
            if user_msg_result.data:
                user_timestamp = user_msg_result.data[0]["timestamp"]
                # Parse the timestamp and add 1ms
                from datetime import datetime, timedelta
                
                # Fix: Normalize timestamp format from Supabase
                # Supabase sometimes returns timestamps with varying microsecond precision (4-6 digits)
                # Python's fromisoformat expects exactly 6 digits for microseconds
                timestamp_str = user_timestamp.replace('Z', '+00:00')
                
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
                
                user_dt = datetime.fromisoformat(timestamp_str)
                assistant_dt = user_dt + timedelta(milliseconds=1)
                # Format as ISO string with timezone
                message_data["timestamp"] = assistant_dt.isoformat().replace('+00:00', 'Z')
                logger.info(f"Setting ASSISTANT message timestamp to {message_data['timestamp']} (1ms after USER message at {user_timestamp})")
            else:
                logger.warning(f"No USER message found with request_id {request_id}, using default timestamp")
        
        # Debug: Log exactly what we're sending to Supabase
        if "timestamp" in message_data:
            logger.info(f"DEBUG: Inserting message with timestamp field: {message_data['timestamp']}")
        else:
            logger.info(f"DEBUG: Inserting message WITHOUT timestamp field (will use DB default)")
        
        result = supabase.table("messages").insert(message_data).execute()
        
        if not result.data:
            logger.error(f"Failed to save {message_type} message to conversation {conversation_id} - no data returned from insert")
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
        
        return (conversation_id, message_id)

    except Exception as e:
        logger.error(f"Error saving message to database: {str(e)}")
        return None


def update_tool_result_in_db(conversation_id: int, tool_use_id: str, result_content: dict) -> bool:
    """Update a pending tool_result message with the actual result

    Args:
        conversation_id: The conversation ID containing the tool result message
        tool_use_id: The tool_use_id to identify which tool_result to update
        result_content: The actual tool execution result to replace the pending content

    Returns:
        True if update successful, False otherwise
    """
    try:
        logger.info(f"Updating tool_result for tool_use_id={tool_use_id} in conversation={conversation_id}")

        # Find the pending tool_result message
        result = supabase.table("messages")\
            .select("id, tool_content")\
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

        # Update the tool_content with the actual result
        updated_tool_content = [{
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": json.dumps(result_content)
        }]

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

@app.post("/update_api_tokens")
async def update_api_tokens(
    request: TokenUpdateRequest,
    current_user: Dict = Depends(get_current_user)
):
    """Update the user's API tokens (Claude API key and/or Asana token)"""
    try:
        user_id = current_user.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="User not authenticated")
        
        # Handle Claude API key if provided
        if request.claude_api_key is not None:
            if request.claude_api_key.strip():  # Non-empty key
                success = set_encrypted_preference_key(user_id, "claude_api_key", request.claude_api_key.strip())
                if not success:
                    raise HTTPException(status_code=500, detail="Failed to save Claude API key")
            else:  # Empty key - remove it
                success = set_encrypted_preference_key(user_id, "claude_api_key", None)
                if not success:
                    logger.warning(f"Failed to remove Claude API key for user {user_id}")
        
        # Handle Asana token if provided
        if request.asana_access_token is not None:
            if request.asana_access_token.strip():  # Non-empty token
                success = set_encrypted_preference_key(user_id, "asana_access_token", request.asana_access_token.strip())
                if not success:
                    raise HTTPException(status_code=500, detail="Failed to save Asana token")
            else:  # Empty token - remove it
                success = set_encrypted_preference_key(user_id, "asana_access_token", None)
                if not success:
                    logger.warning(f"Failed to remove Asana token for user {user_id}")
        
        return {"message": "API tokens updated successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected exception in update_api_tokens: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update API tokens")

@app.get("/preferences/tokens")
async def get_api_tokens(current_user: Dict = Depends(get_current_user)):
    """Get the user's API token status."""
    try:
        user_id = current_user["sub"]
        
        logger.info(f"🔍 Checking token status for user {user_id}")
        
        # Get both tokens
        claude_token = get_decrypted_preference_key(user_id, 'claude_api_key')
        asana_token = get_decrypted_preference_key(user_id, 'asana_access_token')
        
        # Detailed logging for Claude token
        logger.info(f"  Claude token check:")
        logger.info(f"    Raw value type: {type(claude_token)}")
        logger.info(f"    Raw value is None: {claude_token is None}")
        logger.info(f"    Raw value repr: {repr(claude_token)}")
        if claude_token is not None:
            logger.info(f"    Raw value length: {len(claude_token)}")
            logger.info(f"    Is empty string: {claude_token == ''}")
            logger.info(f"    Is whitespace only: {claude_token.strip() == '' if isinstance(claude_token, str) else 'N/A'}")
        
        # Detailed logging for Asana token
        logger.info(f"  Asana token check:")
        logger.info(f"    Raw value type: {type(asana_token)}")
        logger.info(f"    Raw value is None: {asana_token is None}")
        logger.info(f"    Raw value repr: {repr(asana_token)[:50] + '...' if asana_token and len(repr(asana_token)) > 50 else repr(asana_token)}")
        
        # Updated logic to handle both None and string "None"
        has_claude = claude_token is not None and claude_token != "None" and claude_token != ""
        has_asana = asana_token is not None and asana_token != "None" and asana_token != ""
        
        logger.info(f"  Updated logic results:")
        logger.info(f"    has_claude_token: {has_claude} (checks: not None, not 'None', not empty)")
        logger.info(f"    has_asana_token: {has_asana} (checks: not None, not 'None', not empty)")
        
        # Show what old logic would have given for comparison
        old_has_claude = claude_token is not None
        old_has_asana = asana_token is not None
        logger.info(f"  Old logic would have given:")
        logger.info(f"    has_claude_token: {old_has_claude}, has_asana_token: {old_has_asana}")
        
        return {
            "has_claude_token": has_claude,
            "has_asana_token": has_asana
        }
    except Exception as e:
        logger.error(f"Error getting tokens: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/user/timezone")
async def set_user_timezone_api(
    request: SetTimezoneRequest,
    current_user: dict = Depends(get_current_user)
):
    user_id = current_user.get("sub")
    if not user_id:
        # This should ideally not be reached if Depends(get_current_user) works as expected
        raise HTTPException(status_code=401, detail="User not authenticated")

    success, message = set_user_timezone(user_id, request.timezone)
    if success:
        logger.debug(f"Successfully set timezone for user {user_id} via API: {request.timezone}")
        return {"status": "success", "message": message}
    else:
        # set_user_timezone already logs detailed errors
        # We return a 400 if the timezone string was invalid or if saving failed
        logger.warning(f"Failed to set timezone for user {user_id} via API: {message}")
        raise HTTPException(status_code=400, detail=message)

# ================== USER PREFERENCE ENDPOINTS ==================

@app.get("/user/preference")
async def get_user_preference(
    key: str,
    current_user: Dict = Depends(get_current_user)
):
    """Get a specific user preference value"""
    try:
        user_id = current_user.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="User not authenticated")
        
        # Validate the preference key
        if key not in ALLOWED_PREFERENCE_KEYS:
            raise HTTPException(status_code=400, detail=f"Preference key '{key}' is not allowed")
        
        # Ensure user and preferences exist
        ensure_user_and_prefs(user_id)
        
        # Get the preference value (from unencrypted preferences)
        value = get_preference(user_id, key)
        
        logger.info(f"Retrieved preference '{key}' for user {user_id}")
        return value  # Return the value directly (can be None)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting user preference '{key}' for user {user_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to get user preference")

@app.post("/user/preference")
async def set_user_preference(
    key: str,
    request: Request,  # Get the raw request to read the body
    current_user: Dict = Depends(get_current_user)
):
    """Set a specific user preference value"""
    try:
        user_id = current_user.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="User not authenticated")
        
        # Validate the preference key
        if key not in ALLOWED_PREFERENCE_KEYS:
            raise HTTPException(status_code=400, detail=f"Preference key '{key}' is not allowed")
        
        # Ensure user and preferences exist
        ensure_user_and_prefs(user_id)
        
        # Read the value from the request body
        body = await request.body()
        value = body.decode('utf-8')
        
        # Set the preference value (in unencrypted preferences)
        success = set_preference(user_id, key, value)
        
        if not success:
            raise HTTPException(status_code=500, detail="Failed to save user preference")
        
        logger.info(f"Set preference '{key}' for user {user_id}")
        return {"status": "success", "message": f"Preference '{key}' updated successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error setting user preference '{key}' for user {user_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to set user preference")

# ================== SYNC HELPER FUNCTIONS ==================

def get_last_sync_timestamp(user_id: str, entity_type: str, entity_id: Optional[int] = None) -> Optional[str]:
    """Get the last sync timestamp for a user and entity type"""
    try:
        query = supabase.table("sync_metadata")\
            .select("last_sync_timestamp")\
            .eq("user_id", user_id)\
            .eq("entity_type", entity_type)
        
        if entity_id is not None:
            query = query.eq("entity_id", entity_id)
        else:
            query = query.is_("entity_id", "null")
        
        result = query.execute()
        if result.data:
            return result.data[0]["last_sync_timestamp"]
        return None
    except Exception as e:
        logger.error(f"Error getting last sync timestamp for user {user_id}, entity {entity_type}: {e}")
        return None

def update_last_sync_timestamp(user_id: str, entity_type: str, timestamp: str, entity_id: Optional[int] = None):
    """Update the last sync timestamp for a user and entity type"""
    try:
        data = {
            "user_id": user_id,
            "entity_type": entity_type,
            "last_sync_timestamp": timestamp,
            "entity_id": entity_id
        }
        
        # Use upsert to insert or update
        result = supabase.table("sync_metadata").upsert(data).execute()
        logger.info(f"Updated sync timestamp for user {user_id}, entity {entity_type}: {timestamp}")
        return True
    except Exception as e:
        logger.error(f"Error updating sync timestamp for user {user_id}, entity {entity_type}: {e}")
        return False

@app.get("/sync/metadata")
async def get_sync_metadata(current_user: Dict = Depends(get_current_user)):
    """Get sync metadata for the current user"""
    try:
        user_id = current_user.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="User not authenticated")
        
        result = supabase.table("sync_metadata")\
            .select("*")\
            .eq("user_id", user_id)\
            .execute()
        
        return {"sync_metadata": result.data if result.data else []}
    except Exception as e:
        logger.error(f"Error getting sync metadata: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to get sync metadata")

@app.post("/sync/metadata")
async def update_sync_metadata(
    entity_type: str,
    timestamp: str,
    entity_id: Optional[int] = None,
    current_user: Dict = Depends(get_current_user)
):
    """Update sync metadata for the current user"""
    try:
        user_id = current_user.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="User not authenticated")
        
        success = update_last_sync_timestamp(user_id, entity_type, timestamp, entity_id)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to update sync metadata")
        
        return {"success": True, "message": "Sync metadata updated"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating sync metadata: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to update sync metadata")

# ================== CONVERSATION HELPER FUNCTIONS ==================

def resolve_conversation_id(conversation_id: int, user_id: str) -> Optional[int]:
    """
    Resolve a conversation ID, handling optimistic (negative) IDs by looking them up.
    Returns the actual conversation ID or None if not found.
    """
    try:
        if conversation_id < 0:
            # Look up the real conversation using the optimistic_chat_id
            result = supabase.table("conversations")\
                .select("id")\
                .eq("optimistic_chat_id", str(conversation_id))\
                .eq("user_id", user_id)\
                .is_("deleted_at", "null")\
                .execute()
            
            if result.data:
                return result.data[0]["id"]
            return None
        else:
            # For positive IDs, verify it exists and user owns it
            result = supabase.table("conversations")\
                .select("id")\
                .eq("id", conversation_id)\
                .eq("user_id", user_id)\
                .is_("deleted_at", "null")\
                .execute()
            
            if result.data:
                return conversation_id
            return None
    except Exception as e:
        logger.error(f"Error resolving conversation ID {conversation_id}: {str(e)}")
        return None

# ================== CONVERSATION ENDPOINTS ==================

@app.get("/conversations")
async def get_conversations(
    since: Optional[str] = None,  # ISO timestamp string for incremental sync
    current_user: Dict = Depends(get_current_user)
):
    """Get conversations for the current user with optional incremental sync"""
    try:
        user_id = current_user.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="User not authenticated")
        
        logger.info(f"Getting conversations for user {user_id}, since: {since}")
        
        # Build the query
        query = supabase.table('conversations')\
            .select('*')\
            .eq('user_id', user_id)\
            .order('updated_at', desc=True)
        
        # Add incremental sync filter if provided
        if since:
            try:
                # Parse the timestamp and filter for records updated after it
                since_timestamp = datetime.fromisoformat(since.replace('Z', '+00:00'))
                # Use slightly older timestamp to avoid edge case timing issues
                # Go back 1 second to catch any conversations that might be missed due to timing precision
                adjusted_timestamp = since_timestamp - timedelta(seconds=1)
                query = query.gte('updated_at', adjusted_timestamp.isoformat())
                logger.info(f"Incremental sync: fetching conversations updated since {adjusted_timestamp} (original: {since_timestamp}, includes deleted)")
                # Note: For incremental sync, we include deleted conversations so client can handle deletions
            except ValueError as e:
                logger.warning(f"Invalid 'since' timestamp format: {since}, error: {e}")
                # Fall back to full sync if timestamp is invalid
        else:
            # For full sync (no 'since'), exclude deleted conversations
            query = query.is_('deleted_at', 'null')
            logger.info(f"Full sync: excluding deleted conversations")
        
        response = query.execute()
        conversations = response.data if response.data else []
        
        # Log detailed information about what we're returning
        if conversations:
            logger.info(f"Found {len(conversations)} conversations for user {user_id}:")
            for conv in conversations[:3]:  # Log first 3 conversations
                logger.info(f"  - ID {conv['id']}: '{conv['title'][:30]}...' updated_at: {conv['updated_at']}")
        else:
            logger.warning(f"No conversations found for user {user_id} with filter since={since}")
        
        # Return with server timestamp for next incremental sync
        result = {
            'conversations': conversations,
            'server_timestamp': datetime.utcnow().isoformat() + 'Z',
            'is_incremental': since is not None,
            'count': len(conversations)
        }
        
        logger.info(f"Returning {len(conversations)} conversations (incremental: {since is not None})")
        return result
        
    except Exception as e:
        logger.error(f"Error getting conversations: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to get conversations")

@app.post("/conversations", response_model=ConversationResponse)
async def create_conversation(
    conversation: ConversationCreate,
    current_user: Dict = Depends(get_current_user)
):
    """Create a new conversation for the authenticated user"""
    user_id = current_user.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        # Insert new conversation
        result = supabase.table("conversations").insert({
            "user_id": user_id,
            "title": conversation.title,
            "source": conversation.source,
            "google_session_id": conversation.google_session_id
        }).execute()
        
        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to create conversation")
        
        row = result.data[0]
        return ConversationResponse(
            id=row["id"],
            user_id=row["user_id"],
            title=row["title"],
            created_at=row["created_at"],
            last_message_time=row["last_message_time"],
            source=row["source"],
            google_session_id=row.get("google_session_id"),
            deleted_at=row.get("deleted_at")
        )
    except Exception as e:
        logger.error(f"Error creating conversation for user {user_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to create conversation")

@app.get("/conversations/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(
    conversation_id: int,
    current_user: Dict = Depends(get_current_user)
):
    """Get a specific conversation by ID (user must own it)"""
    user_id = current_user.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        # Resolve the actual conversation ID (handles optimistic IDs)
        actual_conversation_id = resolve_conversation_id(conversation_id, user_id)
        if actual_conversation_id is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        
        # Fetch the full conversation data
        result = supabase.table("conversations")\
            .select("*")\
            .eq("id", actual_conversation_id)\
            .eq("user_id", user_id)\
            .is_("deleted_at", "null")\
            .execute()
        
        if not result.data:
            raise HTTPException(status_code=404, detail="Conversation not found")
        
        row = result.data[0]
        return ConversationResponse(
            id=row["id"],
            user_id=row["user_id"],
            title=row["title"],
            created_at=row["created_at"],
            last_message_time=row["last_message_time"],
            source=row["source"],
            google_session_id=row.get("google_session_id"),
            deleted_at=row.get("deleted_at")
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting conversation {conversation_id} for user {user_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to get conversation")

@app.put("/conversations/{conversation_id}", response_model=ConversationResponse)
async def update_conversation(
    conversation_id: int,
    update_data: ConversationUpdate,
    current_user: Dict = Depends(get_current_user)
):
    """Update conversation title (user must own it)"""
    user_id = current_user.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        # Build update dict
        updates = {}
        if update_data.title is not None:
            updates["title"] = update_data.title
        
        if not updates:
            # No updates provided, just return current conversation
            return await get_conversation(conversation_id, current_user)
        
        # Resolve the actual conversation ID (handles optimistic IDs)
        actual_conversation_id = resolve_conversation_id(conversation_id, user_id)
        if actual_conversation_id is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        
        # Update the conversation
        result = supabase.table("conversations")\
            .update(updates)\
            .eq("id", actual_conversation_id)\
            .eq("user_id", user_id)\
            .is_("deleted_at", "null")\
            .execute()
        
        if not result.data:
            raise HTTPException(status_code=404, detail="Conversation not found")
        
        row = result.data[0]
        return ConversationResponse(
            id=row["id"],
            user_id=row["user_id"],
            title=row["title"],
            created_at=row["created_at"],
            last_message_time=row["last_message_time"],
            source=row["source"],
            google_session_id=row.get("google_session_id"),
            deleted_at=row.get("deleted_at")
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating conversation {conversation_id} for user {user_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to update conversation")

@app.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: int,
    current_user: Dict = Depends(get_current_user)
):
    """Delete a specific conversation by ID (user must own it) - uses soft delete with tombstone record"""
    user_id = current_user.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        # Resolve the actual conversation ID (handles optimistic IDs)
        actual_conversation_id = resolve_conversation_id(conversation_id, user_id)
        if actual_conversation_id is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        
        # Check if already deleted
        verify_result = supabase.table("conversations")\
            .select("deleted_at")\
            .eq("id", actual_conversation_id)\
            .execute()
        
        if verify_result.data and verify_result.data[0].get("deleted_at") is not None:
            raise HTTPException(status_code=404, detail="Conversation not found")  # Already deleted
        
        # Soft delete: set deleted_at timestamp instead of hard delete
        result = supabase.table("conversations").update({
            "deleted_at": "now()"
        }).eq("id", actual_conversation_id).eq("user_id", user_id).execute()
        
        if not result.data:
            raise HTTPException(status_code=404, detail="Conversation not found")
        
        logger.info(f"Successfully soft-deleted conversation {actual_conversation_id} (requested: {conversation_id}) for user {user_id}")
        return {"message": "Conversation deleted successfully", "conversation_id": conversation_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting conversation {conversation_id} for user {user_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to delete conversation")

@app.delete("/conversations")
async def delete_all_conversations(current_user: Dict = Depends(get_current_user)):
    """Delete all conversations for the authenticated user"""
    user_id = current_user.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="User not authenticated")

    try:
        # Soft delete all conversations for user by setting deleted_at timestamp
        # This is consistent with single conversation delete behavior
        result = supabase.table("conversations").update({
            "deleted_at": "now()"
        }).eq("user_id", user_id).is_("deleted_at", "null").execute()

        deleted_count = len(result.data) if result.data else 0
        logger.info(f"Successfully soft-deleted {deleted_count} conversations for user {user_id}")

        return {"message": "All conversations deleted successfully", "deleted_count": deleted_count}
    except Exception as e:
        logger.error(f"Error deleting all conversations for user {user_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to delete conversations")

@app.put("/conversations/{conversation_id}/last_message_time")
async def update_conversation_last_message_time(
    conversation_id: int,
    current_user: Dict = Depends(get_current_user)
):
    """Update the last_message_time for a conversation to now()"""
    user_id = current_user.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        # Resolve the actual conversation ID (handles optimistic IDs)
        actual_conversation_id = resolve_conversation_id(conversation_id, user_id)
        if actual_conversation_id is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        
        # Update the last message time
        result = supabase.table("conversations").update({
            "last_message_time": "now()",
            "updated_at": "now()"  # Critical: update this so incremental sync catches conversation updates
        }).eq("id", actual_conversation_id).eq("user_id", user_id).execute()
        
        if not result.data:
            raise HTTPException(status_code=404, detail="Conversation not found")
        
        return {"message": "Last message time updated"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating last message time for conversation {conversation_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to update last message time")

# ================== MESSAGE ENDPOINTS ==================

@app.get("/conversations/{conversation_id}/messages")
async def get_messages(
    conversation_id: int,
    since_timestamp: Optional[float] = None,  # Unix timestamp for incremental sync
    current_user: Dict = Depends(get_current_user)
):
    """Get messages for a conversation with optional incremental sync"""
    from datetime import datetime
    
    try:
        user_id = current_user.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="User not authenticated")
        
        logger.info(f"Getting messages for conversation {conversation_id}, user {user_id}, since_timestamp: {since_timestamp}")
        
        # Resolve the actual conversation ID (handles optimistic IDs)
        actual_conversation_id = resolve_conversation_id(conversation_id, user_id)
        if actual_conversation_id is None:
            logger.warning(f"No conversation found for conversation ID {conversation_id}")
            raise HTTPException(status_code=404, detail="Conversation not found")
        
        # Build the messages query - explicitly select fields including cancelled
        # Include ALL messages (including cancelled ones) so client can handle them appropriately
        # Filter out tool messages (tool_use, tool_result) - only send text messages to Android client
        query = supabase.table("messages")\
            .select("id, conversation_id, content, message_sender, content_type, timestamp, updated_at, request_id, cancelled")\
            .eq("conversation_id", actual_conversation_id)\
            .neq("content_type", "tool_use")\
            .neq("content_type", "tool_result")\
            .order("timestamp", desc=False)
        
        # Add incremental sync filter if provided
        if since_timestamp:
            # Convert Unix timestamp to ISO format for Supabase
            since_datetime = datetime.fromtimestamp(since_timestamp).isoformat()
            # Use OR to catch both new messages and updates (like cancellations)
            query = query.or_(f"timestamp.gt.{since_datetime},updated_at.gt.{since_datetime}")
            logger.info(f"Incremental sync: fetching messages created or updated since {since_datetime}")
        
        
        response = query.execute()
        messages = response.data if response.data else []
        
        # Log raw messages from database
        logger.info(f"Fetched {len(messages)} messages from database for conversation {actual_conversation_id} (including cancelled)")
        
        # Update conversation_id in messages to use the actual server-backed ID
        # This ensures clients always receive messages with positive server-backed IDs
        for message in messages:
            message['conversation_id'] = actual_conversation_id
            # Ensure cancelled field is present (None if not cancelled, timestamp if cancelled)
            if 'cancelled' not in message:
                message['cancelled'] = None
            # Map message_sender -> message_type for API compatibility with Android client
            if 'message_sender' in message:
                message['message_type'] = message['message_sender']
                del message['message_sender']
            # Debug logging to diagnose timestamp
            logger.info(f"Message ID {message.get('id')}: type={message.get('message_type')}, cancelled={message.get('cancelled')}, timestamp={message.get('timestamp')}, content_preview={message.get('content', '')[:50]}")
        
        # Return with server timestamp for next incremental sync
        result = {
            'messages': messages,
            'conversation_id': actual_conversation_id,  # Return the resolved server-backed ID, not the parameter
            'server_timestamp': datetime.utcnow().isoformat() + 'Z',
            'is_incremental': since_timestamp is not None,
            'count': len(messages),
            'includes_cancelled': True  # Signal to client that cancelled messages are included
        }
        
        logger.info(f"Returning {len(messages)} messages for conversation {conversation_id} (incremental: {since_timestamp is not None})")
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting messages for conversation {conversation_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to get messages")

@app.post("/messages", response_model=MessageResponse)
async def create_message(
    message: MessageCreate,
    current_user: Dict = Depends(get_current_user)
):
    """Create a new message in a conversation (user must own the conversation)"""
    user_id = current_user.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        # Handle optimistic chat IDs (negative IDs)
        actual_conversation_id = message.conversation_id
        if message.conversation_id < 0:
            # Look up the real conversation using the optimistic_chat_id
            conv_result = supabase.table("conversations")\
                .select("id")\
                .eq("optimistic_chat_id", str(message.conversation_id))\
                .eq("user_id", user_id)\
                .is_("deleted_at", "null")\
                .execute()
            
            if conv_result.data:
                actual_conversation_id = conv_result.data[0]["id"]
                logger.info(f"Creating message for optimistic chat ID {message.conversation_id}, real ID {actual_conversation_id}")
            else:
                raise HTTPException(status_code=404, detail="Conversation not found")
        else:
            # First verify user owns the conversation
            conv_result = supabase.table("conversations").select("id").eq("id", message.conversation_id).eq("user_id", user_id).execute()
            if not conv_result.data:
                raise HTTPException(status_code=404, detail="Conversation not found")
        
        # Create the message with the actual conversation ID
        message_data = {
            "conversation_id": actual_conversation_id,
            "content": message.content,
            "message_sender": message.message_type,  # Note: message_type field from client maps to message_sender in DB
            "content_type": "text",  # REST API only supports text messages for now
            "request_id": message.request_id
        }
        
        # Include timestamp if provided to preserve message order
        if message.timestamp:
            message_data["timestamp"] = message.timestamp
            logger.info(f"Using client-provided timestamp for message: {message.timestamp}")
        else:
            logger.info(f"No timestamp provided for message, will use database default")
        
        # Log the data being sent to database
        logger.info(f"Inserting message to database with data: {json.dumps(message_data, default=str)}")
        
        result = supabase.table("messages").insert(message_data).execute()
        
        # Log what was actually saved
        if result.data:
            saved_msg = result.data[0]
            logger.info(f"Message saved to database - ID: {saved_msg.get('id')}, timestamp from DB: {saved_msg.get('timestamp')}, request_id: {saved_msg.get('request_id')}")
        
        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to create message")
        
        # Update conversation last_message_time and updated_at for incremental sync
        supabase.table("conversations").update({
            "last_message_time": "now()",
            "updated_at": "now()"  # Critical: update this so incremental sync catches new messages
        }).eq("id", actual_conversation_id).execute()
        
        row = result.data[0]
        return MessageResponse(
            id=row["id"],
            conversation_id=row["conversation_id"],
            content=row["content"],
            message_type=row["message_sender"],
            timestamp=row["timestamp"],
            request_id=row.get("request_id")
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating message: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to create message")

@app.get("/conversations/{conversation_id}/messages/count")
async def get_message_count(
    conversation_id: int,
    current_user: Dict = Depends(get_current_user)
):
    """Get the count of messages in a conversation"""
    user_id = current_user.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        # Resolve the actual conversation ID (handles optimistic IDs)
        actual_conversation_id = resolve_conversation_id(conversation_id, user_id)
        if actual_conversation_id is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        
        # Get message count
        result = supabase.table("messages").select("id", count="exact").eq("conversation_id", actual_conversation_id).execute()
        
        return {"count": result.count}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting message count for conversation {conversation_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to get message count")

@app.post("/conversations/{conversation_id}/check-retry")
async def check_and_retry_failed_messages(
    conversation_id: int,
    current_user: Dict = Depends(get_current_user)
):
    """Check for error messages and retry getting assistant response from Claude"""
    user_id = current_user.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        # Resolve the actual conversation ID (handles optimistic IDs)
        actual_conversation_id = resolve_conversation_id(conversation_id, user_id)
        if actual_conversation_id is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        
        # Get all messages for this conversation
        result = supabase.table("messages")\
            .select("*")\
            .eq("conversation_id", actual_conversation_id)\
            .order("timestamp", desc=False)\
            .execute()
        
        if not result.data:
            return {"retried": False, "reason": "No messages in conversation"}
        
        messages = result.data
        
        # Find the last message
        if messages:
            last_message = messages[-1]
            
            # Check if it's an error message (ASSISTANT type with JSON error content)
            if last_message.get("message_sender") == "ASSISTANT":
                try:
                    content = json.loads(last_message.get("content", ""))
                    if content.get("type") == "error":
                        error_code = content.get("code")
                        request_id = content.get("request_id")

                        logger.info(f"Found error message with code {error_code}, will retry getting assistant response")

                        # Find the user message before this error
                        user_message_content = None
                        for i in range(len(messages) - 2, -1, -1):
                            if messages[i].get("message_sender") == "USER":
                                user_message_content = messages[i].get("content")
                                break
                        
                        if user_message_content:
                            # Delete the error message immediately to prevent duplicate retries
                            logger.info(f"Deleting error message {last_message.get('id')} before retry")
                            supabase.table("messages")\
                                .delete()\
                                .eq("id", last_message.get("id"))\
                                .execute()
                            
                            # Create a temporary session ID for this retry
                            session_id = f"retry_{actual_conversation_id}_{int(time.time())}"
                            
                            # Build conversation history in chat session (exclude the error message)
                            chat_messages = []
                            for msg in messages[:-1]:  # Exclude the last error message
                                if msg.get("message_sender") == "USER":
                                    chat_messages.append({"role": "user", "content": msg.get("content")})
                                elif msg.get("message_sender") == "ASSISTANT":
                                    # Skip if it's an error message
                                    try:
                                        msg_content = json.loads(msg.get("content", ""))
                                        if msg_content.get("type") != "error":
                                            chat_messages.append({"role": "assistant", "content": msg.get("content")})
                                    except:
                                        # Regular assistant message
                                        chat_messages.append({"role": "assistant", "content": msg.get("content")})
                            
                            # Set up the chat session for retry
                            await set_chat_messages(session_id, chat_messages)
                            
                            # Get the anthropic client using the standard method
                            current_anthropic_client = await get_anthropic_client(user_id)
                            if not current_anthropic_client:
                                logger.warning(f"No Claude API key configured for user {user_id}")
                                return {
                                    "retried": False,
                                    "reason": "No Claude API key configured",
                                    "error_code": "CLAUDE_API_KEY_MISSING"
                                }
                            
                            try:
                                # Call Claude API using the same approach as process_message_task
                                logger.info(f"Calling Claude API to retry message for conversation {actual_conversation_id}")
                                
                                # For retry, we don't need streaming (simpler error handling)
                                retry_coroutine = await call_claude_api(
                                    current_anthropic_client, 
                                    session_id, 
                                    stream=False,  # No streaming for retries
                                    conversation_id=actual_conversation_id
                                )
                                
                                # Create task to avoid coroutine reuse
                                retry_task = asyncio.create_task(retry_coroutine)
                                
                                # Use timeout like in normal flow
                                response = await asyncio.wait_for(retry_task, timeout=60.0)
                                
                                # Extract text response
                                assistant_response = ""
                                if response.content and response.content[0].type == 'text':
                                    assistant_response = response.content[0].text
                                
                                if assistant_response:
                                    # Save the new assistant response (error message already deleted)
                                    logger.info(f"Saving new assistant response for conversation {actual_conversation_id}")
                                    save_result = save_message_to_db(
                                        user_id=user_id,
                                        conversation_id=actual_conversation_id,
                                        content=assistant_response,
                                        message_sender="ASSISTANT",
                                        request_id=request_id,
                                        content_type="text"
                                    )
                                    
                                    if save_result:
                                        saved_conversation_id, message_id = save_result
                                        logger.info(f"Successfully saved assistant response as message {message_id}")
                                        
                                        return {
                                            "retried": True,
                                            "reason": "Successfully got assistant response",
                                            "response": assistant_response,
                                            "message_id": message_id
                                        }
                                    else:
                                        logger.error(f"Failed to save assistant response to database")
                                        return {
                                            "retried": False,
                                            "reason": "Failed to save response to database"
                                        }
                                else:
                                    logger.warning(f"Got empty response from Claude")
                                    return {
                                        "retried": False,
                                        "reason": "Got empty response from Claude"
                                    }
                                    
                            except AuthenticationError as auth_error:
                                logger.warning(f"Claude authentication failed during retry: {str(auth_error)}")
                                # Re-save the error message since we deleted it
                                error_content = json.dumps({
                                    "type": "error",
                                    "code": "CLAUDE_AUTHENTICATION_ERROR",
                                    "message": f"Claude API authentication failed: {str(auth_error)}. Please check your Claude API Key in settings.",
                                    "request_id": request_id
                                })
                                save_message_to_db(
                                    user_id=user_id,
                                    conversation_id=actual_conversation_id,
                                    content=error_content,
                                    message_sender="ASSISTANT",
                                    request_id=request_id,
                                    content_type="text"
                                )
                                return {
                                    "retried": False,
                                    "reason": "Claude authentication still failing",
                                    "error": str(auth_error)
                                }
                            except Exception as api_error:
                                logger.error(f"Claude API error during retry: {str(api_error)}")
                                # Re-save error message with new error (since we deleted the old one)
                                new_error_content = json.dumps({
                                    "type": "error",
                                    "code": "CLAUDE_API_ERROR",
                                    "message": f"Claude API error: {str(api_error)}",
                                    "request_id": request_id
                                })
                                
                                save_message_to_db(
                                    user_id=user_id,
                                    conversation_id=actual_conversation_id,
                                    content=new_error_content,
                                    message_sender="ASSISTANT",
                                    request_id=request_id,
                                    content_type="text"
                                )
                                
                                return {
                                    "retried": False,
                                    "reason": "Claude API error",
                                    "error": str(api_error)
                                }
                        else:
                            return {"retried": False, "reason": "No user message found to retry"}
                            
                except json.JSONDecodeError:
                    # Not a JSON error message, it's a regular assistant message
                    pass
        
        return {"retried": False, "reason": "No error message found"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error checking for retry in conversation {conversation_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to check for retry")

@app.get("/requests/{request_id}/state")
async def get_request_state_endpoint(
    request_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Get the state of a specific request"""
    state = await get_request_state(request_id)
    if not state:
        raise HTTPException(status_code=404, detail="Request state not found")
    
    # Verify the request belongs to the current user
    if state.get("metadata", {}).get("user_id") != current_user.get("sub"):
        raise HTTPException(status_code=403, detail="Access denied")
    
    return state

@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
async def catch_all(path: str, request: Request):
    print(f"Unmatched request: {request.method} /{path}")
    return JSONResponse({"error": "Not found"}, status_code=404)

async def process_message_task(websocket, session_id, session_conversation_id, user_id, message, request_id, client_conversation_id=None, client_message_id=None, client_timestamp=None):
    """Process a single message in a cancellable task"""
    # Helper function to safely send to WebSocket
    async def safe_websocket_send(payload):
        """Send payload to WebSocket, handling disconnection gracefully"""
        try:
            message_json = json.dumps(payload)
            logger.debug(f"Attempting to send WebSocket message (type: {payload.get('type', 'unknown')}, size: {len(message_json)} bytes)")
            await websocket.send_text(message_json)
            logger.debug(f"Successfully sent WebSocket message of type: {payload.get('type', 'unknown')}")
            return True
        except Exception as e:
            logger.warning(f"WebSocket send failed for session {session_id}: {str(e)} - Message type: {payload.get('type', 'unknown')}")
            return False
    
    try:
        # Track request state as pending
        await set_request_state(request_id, "pending", {
            "session_id": session_id,
            "conversation_id": session_conversation_id,
            "user_id": user_id,
            "message": message[:100],  # Store first 100 chars for debugging
            "start_time": time.time()
        })
        
        messages = await get_chat_messages(session_id)

        # Check if conversation history is complete (every tool_use has a tool_result)
        # This handles race conditions where a new message arrives while a tool is executing
        has_incomplete_tool = False
        if len(messages) > 0:
            for i, msg in enumerate(messages):
                if msg.get('role') == 'assistant' and isinstance(msg.get('content'), list):
                    for block in msg['content']:
                        if isinstance(block, dict) and block.get('type') == 'tool_use':
                            # Check if next message is a tool_result for this tool_use
                            tool_use_id = block.get('id')
                            if i + 1 >= len(messages):
                                has_incomplete_tool = True
                                break
                            next_msg = messages[i + 1]
                            if next_msg.get('role') != 'user':
                                has_incomplete_tool = True
                                break
                            next_content = next_msg.get('content', [])
                            if not isinstance(next_content, list):
                                has_incomplete_tool = True
                                break
                            # Check if any block in next message is a tool_result for our tool_use_id
                            has_result = False
                            for result_block in next_content:
                                if isinstance(result_block, dict) and \
                                   result_block.get('type') == 'tool_result' and \
                                   result_block.get('tool_use_id') == tool_use_id:
                                    has_result = True
                                    break
                            if not has_result:
                                has_incomplete_tool = True
                                break
                if has_incomplete_tool:
                    break

        # If we have an existing conversation but no messages in Redis OR incomplete tool execution,
        # populate from database. This handles reconnection scenarios and race conditions.
        # IMPORTANT: Also check for optimistic conversations (negative IDs) that may have messages in DB
        if (len(messages) == 0 or has_incomplete_tool) and session_conversation_id:
            if has_incomplete_tool:
                logger.warning(f"Redis session for conversation {session_conversation_id} has incomplete tool execution, reloading from database")
            elif len(messages) == 0:
                logger.info(f"Redis session empty for conversation {session_conversation_id}, loading history from database")
            try:
                # Get all messages from database for this conversation
                query = supabase.table("messages")\
                    .select("id, content, message_sender, timestamp, request_id, cancelled, content_type, tool_content")\
                    .eq("conversation_id", session_conversation_id)\
                    .order("timestamp", desc=False)

                response = query.execute()
                db_messages = response.data if response.data else []

                # Build Redis session from database messages
                redis_messages = []
                # First pass: identify tool_use IDs that have corresponding tool_results
                tool_use_ids_with_results = set()
                for msg in db_messages:
                    if msg.get('content_type') == 'tool_result' and msg.get('tool_content'):
                        for block in msg['tool_content']:
                            if isinstance(block, dict) and block.get('tool_use_id'):
                                tool_use_ids_with_results.add(block['tool_use_id'])

                # Second pass: build messages, skipping incomplete tool_use blocks
                for msg in db_messages:
                    # Skip cancelled messages
                    if msg.get('cancelled'):
                        continue

                    content_type = msg.get('content_type', 'text')
                    tool_content = msg.get('tool_content')

                    # Handle tool_use messages - skip if no corresponding tool_result
                    if content_type == 'tool_use' and tool_content:
                        tool_use_id = None
                        for block in tool_content:
                            if isinstance(block, dict) and block.get('id'):
                                tool_use_id = block['id']
                                break

                        if tool_use_id and tool_use_id not in tool_use_ids_with_results:
                            logger.warning(f"Skipping incomplete tool_use (no result): {tool_use_id}")
                            continue

                        redis_messages.append({"role": "assistant", "content": tool_content})
                    elif content_type == 'tool_result' and tool_content:
                        redis_messages.append({"role": "user", "content": tool_content})
                    # Handle regular text messages
                    elif msg['message_sender'] == 'USER':
                        redis_messages.append({"role": "user", "content": msg['content']})
                    elif msg['message_sender'] == 'ASSISTANT':
                        redis_messages.append({"role": "assistant", "content": msg['content']})

                # Populate Redis session with conversation history
                if redis_messages:
                    await set_chat_messages(session_id, redis_messages)
                    messages = redis_messages
                    logger.info(f"Populated Redis session with {len(redis_messages)} messages from database")
                
            except Exception as e:
                logger.error(f"Error loading conversation history from database: {str(e)}")
                # Continue with empty context if database load fails
        
        logger.info(f"Processing message in session {session_id}, conversation {session_conversation_id}, context length: {len(messages)}")
        
        # Save user message to database and update session_conversation_id
        logger.info(f"About to save user message. Current session_conversation_id: {session_conversation_id}")
        save_result = save_message_to_db(user_id, session_conversation_id, message, "USER", request_id, client_conversation_id, client_timestamp, content_type="text")
        if save_result is None:
            logger.error("Failed to save user message to database")
            error_payload = {
                "type": "error",
                "code": "DATABASE_ERROR",
                "message": "Failed to save message to database",
                "request_id": request_id,
                "client_conversation_id": client_conversation_id
            }
            await safe_websocket_send(error_payload)
            return session_conversation_id
        
        real_conversation_id, user_message_id = save_result
        logger.info(f"After saving user message. Updated session_conversation_id: {real_conversation_id}, message_id: {user_message_id}")
        
        # Update optimistic → real mapping if client provided optimistic ID
        if client_conversation_id and client_conversation_id < 0 and real_conversation_id:
            if redis_managers and "session_mappings" in redis_managers:
                try:
                    await redis_managers["session_mappings"].set_mapping(
                        session_id, client_conversation_id, real_conversation_id
                    )
                    logger.info(f"Mapped optimistic ID {client_conversation_id} → real ID {real_conversation_id}")
                except ValueError as e:
                    # This shouldn't happen in normal operation, but if it does, log it and continue
                    logger.error(f"Failed to set optimistic mapping: {str(e)}")
                    # Send error to client so they know something went wrong
                    error_payload = {
                        "type": "error",
                        "code": "MAPPING_CONFLICT",
                        "message": "Conversation mapping conflict detected. Please refresh the page.",
                        "request_id": request_id,
                        "conversation_id": real_conversation_id
                    }
                    await safe_websocket_send(error_payload)
        
        # CRITICAL FIX: Update WebSocket listener IMMEDIATELY when conversation ID changes
        # This must happen BEFORE we start broadcasting to the new conversation ID
        if real_conversation_id and real_conversation_id != session_conversation_id:
            logger.info(f"Conversation ID changed from {session_conversation_id} to {real_conversation_id}, updating WebSocket listener immediately")
            try:
                # This moves the WebSocket registration from old to new conversation ID
                # The updated register_conversation_websocket method now automatically removes
                # the optimistic registration when registering with a real ID, preventing duplicates
                await update_websocket_conversation(session_id, session_conversation_id, real_conversation_id, websocket)
            except Exception as e:
                logger.error(f"Failed to update WebSocket conversation, continuing with original: {str(e)}")
                # Don't fail the entire message processing if the update fails
        
        # Use real conversation ID for rest of processing
        session_conversation_id = real_conversation_id

        # Wait for any pending tool executions to complete before processing new message
        # This prevents race conditions where new messages arrive while tools are still executing
        if session_conversation_id:
            pending_count = await get_pending_tools_count(session_conversation_id)
            if pending_count > 0:
                logger.info(f"Waiting for {pending_count} pending tool(s) to complete before processing new message")
                tools_completed = await wait_for_pending_tools(session_conversation_id, timeout_seconds=5.0)
                if tools_completed:
                    logger.info(f"Pending tools completed, will reload history from database")
                else:
                    logger.warning(f"Timeout waiting for pending tools, proceeding anyway")

        # Add message to Redis IMMEDIATELY after getting real conversation ID
        # Use asyncio.shield to protect this from cancellation - MUST complete even if task is cancelled
        try:
            async with chat_sessions_lock:
                # CRITICAL: For new conversations, check if we need to load existing messages first
                # This handles the case where multiple messages were queued offline
                current_messages = await get_chat_messages(session_id)
                if len(current_messages) == 0 and session_conversation_id:
                    # Try to load any existing messages from the database
                    logger.info(f"Loading existing messages for conversation {session_conversation_id} before adding new message")
                    try:
                        query = supabase.table("messages")\
                            .select("id, content, message_sender, timestamp, request_id, cancelled, content_type, tool_content")\
                            .eq("conversation_id", session_conversation_id)\
                            .order("timestamp", desc=False)

                        response = query.execute()
                        db_messages = response.data if response.data else []

                        # Build Redis session from database messages (excluding current message)
                        redis_messages = []
                        # First pass: identify tool_use IDs that have corresponding tool_results
                        tool_use_ids_with_results = set()
                        for msg in db_messages:
                            if msg.get('content_type') == 'tool_result' and msg.get('tool_content'):
                                for block in msg['tool_content']:
                                    if isinstance(block, dict) and block.get('tool_use_id'):
                                        tool_use_ids_with_results.add(block['tool_use_id'])

                        # Second pass: build messages, skipping incomplete tool_use blocks
                        for msg in db_messages:
                            # Skip cancelled messages and the current message (by request_id)
                            if msg.get('cancelled') or msg.get('request_id') == request_id:
                                continue

                            content_type = msg.get('content_type', 'text')
                            tool_content = msg.get('tool_content')

                            # Handle tool_use messages - skip if no corresponding tool_result
                            if content_type == 'tool_use' and tool_content:
                                tool_use_id = None
                                for block in tool_content:
                                    if isinstance(block, dict) and block.get('id'):
                                        tool_use_id = block['id']
                                        break

                                if tool_use_id and tool_use_id not in tool_use_ids_with_results:
                                    logger.warning(f"Skipping incomplete tool_use (no result): {tool_use_id}")
                                    continue

                                redis_messages.append({"role": "assistant", "content": tool_content})
                            elif content_type == 'tool_result' and tool_content:
                                redis_messages.append({"role": "user", "content": tool_content})
                            # Handle regular text messages
                            elif msg['message_sender'] == 'USER':
                                redis_messages.append({"role": "user", "content": msg['content']})
                            elif msg['message_sender'] == 'ASSISTANT':
                                redis_messages.append({"role": "assistant", "content": msg['content']})

                        if redis_messages:
                            await set_chat_messages(session_id, redis_messages)
                            logger.info(f"Pre-loaded {len(redis_messages)} existing messages into Redis session")
                    except Exception as e:
                        logger.error(f"Failed to pre-load existing messages: {e}")
                        # Continue without context rather than fail

                # CRITICAL FIX: Check if last message is a tool_result
                # If so, insert a cancelled assistant message to prevent Claude API error
                # when consecutive user messages get merged (tool_result + new text)
                current_messages = await get_chat_messages(session_id)
                if current_messages and len(current_messages) > 0:
                    last_msg = current_messages[-1]
                    if last_msg.get('role') == 'user' and isinstance(last_msg.get('content'), list):
                        # Check if last message contains a tool_result block
                        has_tool_result = any(
                            isinstance(block, dict) and block.get('type') == 'tool_result'
                            for block in last_msg['content']
                        )

                        if has_tool_result:
                            logger.info(f"Last message contains tool_result, inserting cancelled assistant message to prevent merging with new user message")

                            # Insert cancelled assistant message to database
                            separator_text = "..."  # Minimal placeholder text

                            try:
                                # Insert directly to DB with cancelled=now() since save_message_to_db doesn't support it
                                from datetime import datetime
                                separator_data = {
                                    "conversation_id": session_conversation_id,
                                    "content": separator_text,
                                    "message_sender": "ASSISTANT",
                                    "content_type": "text",
                                    "cancelled": datetime.utcnow().isoformat() + 'Z'
                                }

                                separator_result = supabase.table("messages").insert(separator_data).execute()
                                if separator_result.data:
                                    logger.info(f"Inserted cancelled assistant separator message to database for conversation {session_conversation_id}")
                                else:
                                    logger.error(f"Failed to insert separator - no data returned")

                                # Also add to Redis session so it's available immediately
                                await add_chat_message(session_id, {"role": "assistant", "content": separator_text})
                                logger.info(f"Added cancelled assistant separator to Redis session")

                            except Exception as e:
                                logger.error(f"Failed to insert cancelled assistant separator: {e}")
                                # Continue anyway - the merge might still work

                # Now add the current message
                await asyncio.shield(add_chat_message(session_id, {"role": "user", "content": message}))
                logger.info(f"Added current message to Redis session for conversation {session_conversation_id}")

                # CRITICAL: After adding message, check if Redis now has incomplete tool_use blocks
                # This handles race condition where tool_result was just saved to DB but not in Redis
                current_redis_messages = await get_chat_messages(session_id)
                has_incomplete_after_add = False
                for i, msg in enumerate(current_redis_messages):
                    if msg.get('role') == 'assistant' and isinstance(msg.get('content'), list):
                        for block in msg['content']:
                            if isinstance(block, dict) and block.get('type') == 'tool_use':
                                tool_use_id = block.get('id')
                                if i + 1 >= len(current_redis_messages):
                                    has_incomplete_after_add = True
                                    break
                                next_msg = current_redis_messages[i + 1]
                                if next_msg.get('role') != 'user':
                                    has_incomplete_after_add = True
                                    break
                                next_content = next_msg.get('content', [])
                                if not isinstance(next_content, list):
                                    has_incomplete_after_add = True
                                    break
                                has_result = False
                                for result_block in next_content:
                                    if isinstance(result_block, dict) and \
                                       result_block.get('type') == 'tool_result' and \
                                       result_block.get('tool_use_id') == tool_use_id:
                                        has_result = True
                                        break
                                if not has_result:
                                    has_incomplete_after_add = True
                                    break
                    if has_incomplete_after_add:
                        break

                # If Redis has incomplete tools after adding message, reload from DB
                if has_incomplete_after_add and session_conversation_id:
                    logger.warning(f"Redis has incomplete tool_use after adding message, reloading from database for conversation {session_conversation_id}")
                    try:
                        query = supabase.table("messages")\
                            .select("id, content, message_sender, timestamp, request_id, cancelled, content_type, tool_content")\
                            .eq("conversation_id", session_conversation_id)\
                            .order("timestamp", desc=False)

                        response = query.execute()
                        db_messages = response.data if response.data else []

                        # Build complete history from DB, skipping incomplete tool_use
                        redis_messages = []
                        tool_use_ids_with_results = set()
                        for msg in db_messages:
                            if msg.get('content_type') == 'tool_result' and msg.get('tool_content'):
                                for block in msg['tool_content']:
                                    if isinstance(block, dict) and block.get('tool_use_id'):
                                        tool_use_ids_with_results.add(block['tool_use_id'])

                        for msg in db_messages:
                            if msg.get('cancelled'):
                                continue

                            content_type = msg.get('content_type', 'text')
                            tool_content = msg.get('tool_content')

                            if content_type == 'tool_use' and tool_content:
                                tool_use_id = None
                                for block in tool_content:
                                    if isinstance(block, dict) and block.get('id'):
                                        tool_use_id = block['id']
                                        break
                                if tool_use_id and tool_use_id not in tool_use_ids_with_results:
                                    logger.warning(f"Skipping incomplete tool_use in reload: {tool_use_id}")
                                    continue
                                redis_messages.append({"role": "assistant", "content": tool_content})
                            elif content_type == 'tool_result' and tool_content:
                                redis_messages.append({"role": "user", "content": tool_content})
                            elif msg['message_sender'] == 'USER':
                                redis_messages.append({"role": "user", "content": msg['content']})
                            elif msg['message_sender'] == 'ASSISTANT':
                                redis_messages.append({"role": "assistant", "content": msg['content']})

                        if redis_messages:
                            await set_chat_messages(session_id, redis_messages)
                            logger.info(f"Reloaded {len(redis_messages)} messages from DB to fix incomplete tool execution")
                    except Exception as e:
                        logger.error(f"Failed to reload from DB after incomplete tool detection: {e}")
        except Exception as e:
            logger.error(f"Failed to add message to Redis: {e}")
            # Continue anyway - better to process without full context than to fail
        
        # Track which message IDs this request is responding to
        user_message_ids = get_user_message_ids_since_last_bot(session_conversation_id)
        if redis_managers and "request_messages" in redis_managers:
            await redis_managers["request_messages"].set_messages(
                request_id, user_message_ids, session_conversation_id
            )
            logger.info(f"Tracking request {request_id} responding to message IDs: {user_message_ids}")
        
        # Detect and cancel subset requests
        cancelled_requests = await detect_and_cancel_subset_requests(
            session_conversation_id, user_message_ids, websocket, session_id
        )
        if cancelled_requests:
            logger.info(f"Cancelled {len(cancelled_requests)} subset requests for conversation {session_conversation_id}")

        # Check if this task has been marked for cancellation AFTER critical housekeeping
        # All important work (DB save, Redis add) is done, safe to exit if cancelled
        if asyncio.current_task().cancelled():
            logger.info(f"Task for request {request_id} was cancelled after housekeeping")
            await set_request_state(request_id, "cancelled", {
                "session_id": session_id,
                "conversation_id": session_conversation_id,
                "cancelled_before_api": True
            })
            return session_conversation_id

        current_anthropic_client = await get_anthropic_client(user_id)
        if not current_anthropic_client:
            error_payload_key = {
                "type": "error", 
                "code": "CLAUDE_API_KEY_MISSING",
                "message": "Claude API key is not set. Please configure it in settings.",
                "request_id": request_id,
                "conversation_id": session_conversation_id,  # Include conversation_id for client sync
                "client_conversation_id": client_conversation_id
            }
            await safe_websocket_send(error_payload_key)
            logger.warning(f"User {user_id} attempted to send message without Claude API key.")
            return session_conversation_id


        # Update state to processing
        await set_request_state(request_id, "processing", {
            "session_id": session_id,
            "conversation_id": session_conversation_id,
            "api_call_start": time.time()
        })

        try:
            # Log the tools being sent to Claude for debugging
            logger.info(f"Sending {len(tools)} tools to Claude")
            logger.debug(f"Tool names: {[tool.get('name') for tool in tools]}")
            # Log specifically the launch_app tool structure
            launch_app_tool = next((t for t in tools if t.get('name') == 'launch_app'), None)
            if launch_app_tool:
                logger.info(f"launch_app tool structure: {json.dumps(launch_app_tool, indent=2)}")
            
            # Create the stream/response (auto-detects whether to stream)
            # call_claude_api returns a coroutine that when awaited gives us either:
            # - AsyncStream (when stream=True)
            # - Message response (when stream=False)
            api_coroutine = await call_claude_api(current_anthropic_client, session_id, conversation_id=session_conversation_id)
            
            # The coroutine needs to be awaited to get the actual response
            # Create a task so it can be cancelled if needed
            api_task = asyncio.create_task(api_coroutine)
            
            # Await the task with timeout to get the actual stream or response
            api_result = await asyncio.wait_for(api_task, timeout=60.0)
            
            # Debug logging to understand what we got
            logger.info(f"API result type after awaiting: {type(api_result).__name__}, module: {getattr(type(api_result), '__module__', 'unknown')}")
            
            # Now check if we got a streaming response or a regular message
            # AsyncStream is from anthropic module and is for streaming
            # BetaMessage is for non-streaming responses (including tool calls)
            is_streaming = (
                type(api_result).__name__ == 'AsyncStream' or
                'AsyncStream' in str(type(api_result)) or
                'AsyncMessageStream' in str(type(api_result))
            ) and type(api_result).__name__ != 'BetaMessage'
            
            if is_streaming:
                logger.info(f"[STREAMING] Processing streaming response for request {request_id}")
                # Store the stream for potential cancellation
                if redis_managers and "local_objects" in redis_managers:
                    await redis_managers["local_objects"].add_stream(request_id, api_result)
                
                # Process streaming response
                full_response = ""
                response = None
                
                try:
                    async for chunk in api_result:
                        # Check for cancellation
                        if asyncio.current_task().cancelled():
                            logger.info(f"Stream cancelled for request {request_id}")
                            break
                        
                        # Handle different chunk types
                        if hasattr(chunk, 'type'):
                            if chunk.type == 'content_block_delta' and hasattr(chunk, 'delta'):
                                text_chunk = chunk.delta.text if hasattr(chunk.delta, 'text') else ""
                                full_response += text_chunk
                                
                                # Don't send chunks to client - streaming is only for cancellation support
                                # The complete response will be sent once streaming finishes
                            elif chunk.type == 'message_stop':
                                # Stream complete, create a response object for compatibility
                                response = type('Response', (), {
                                    'content': [type('Content', (), {'type': 'text', 'text': full_response})()],
                                    'stop_reason': 'end_turn'
                                })()
                                break
                except asyncio.TimeoutError:
                    logger.error(f"Streaming timeout for request {request_id}")
                    raise
                
                # If stream was interrupted, create partial response
                if not response and full_response:
                    response = type('Response', (), {
                        'content': [type('Content', (), {'type': 'text', 'text': full_response})()],
                        'stop_reason': 'end_turn'
                    })()
            else:
                logger.info(f"[NON-STREAMING] Processing message response for request {request_id}")
                # For non-streaming (tool calls), we already have the response from awaiting the coroutine
                response = api_result
        except asyncio.TimeoutError:
            logger.error(f"Claude API timeout for request {request_id} after 60 seconds")
            await set_request_state(request_id, "timeout", {
                "session_id": session_id,
                "conversation_id": session_conversation_id,
                "error": "Claude API call timed out after 60 seconds"
            })
            
            # Send timeout error to client
            error_payload = {
                "type": "error",
                "code": "CLAUDE_TIMEOUT",
                "message": "Claude API request timed out. Please try again.",
                "request_id": request_id,
                "conversation_id": session_conversation_id
            }
            await safe_websocket_send(error_payload)
            return session_conversation_id
        except AuthenticationError as auth_error:
            # Handle Claude API authentication errors (401 Unauthorized)
            logger.warning(f"Claude API authentication error for request {request_id}: {str(auth_error)}")
            await set_request_state(request_id, "auth_failed", {
                "session_id": session_id,
                "conversation_id": session_conversation_id,
                "error": str(auth_error)
            })
            
            # Create error payload
            error_payload = {
                "type": "error",
                "code": "CLAUDE_AUTHENTICATION_ERROR",
                "message": f"Claude API authentication failed: {str(auth_error)}. Please check your Claude API Key in settings.",
                "request_id": request_id,
                "conversation_id": session_conversation_id,
                "client_conversation_id": client_conversation_id
            }
            
            # Save error as ASSISTANT message to database (persists even if WebSocket fails)
            error_json_content = json.dumps(error_payload)
            logger.info(f"Saving authentication error as ASSISTANT message for conversation {session_conversation_id}")
            save_result = save_message_to_db(
                user_id=user_id,
                conversation_id=session_conversation_id,
                content=error_json_content,
                message_sender="ASSISTANT",
                request_id=request_id,
                content_type="text"
            )
            if save_result:
                saved_conversation_id, error_message_id = save_result
                logger.info(f"Authentication error saved as message {error_message_id} in conversation {saved_conversation_id}")
            else:
                logger.error(f"Failed to save authentication error message to database")
            
            # Try to send via WebSocket (may fail, but error is persisted in DB)
            await safe_websocket_send(error_payload)
            
            # Don't raise - let the client handle the error gracefully
            return session_conversation_id
        except asyncio.CancelledError:
            # Handle explicit task cancellation
            logger.info(f"Task for request {request_id} was cancelled")
            await set_request_state(request_id, "cancelled", {
                "session_id": session_id,
                "conversation_id": session_conversation_id
            })
            raise
        except Exception as api_error:
            # Check if this is a stream cancellation (from interrupt or subset detection)
            if "stream" in str(api_error).lower() or "cancelled" in str(api_error).lower() or "closed" in str(api_error).lower():
                logger.info(f"Claude stream cancelled for request {request_id} (likely due to interrupt)")
                await set_request_state(request_id, "interrupted", {
                    "session_id": session_id,
                    "conversation_id": session_conversation_id,
                    "reason": "Stream cancelled by newer request"
                })
                
                # Clean up the stream tracking
                if redis_managers and "local_objects" in redis_managers:
                    await redis_managers["local_objects"].remove_stream(request_id)
                
                # Don't send error to client - they already got an interrupt notification
                return session_conversation_id
            
            logger.error(f"Claude API error for request {request_id}: {str(api_error)}")
            logger.error(f"Error type: {type(api_error).__name__}")
            logger.error(f"Error details: {repr(api_error)}")
            # Log if this is a tool-related error
            if "tool" in str(api_error).lower():
                logger.error(f"TOOL ERROR DETECTED: This appears to be a tool-related error")
            await set_request_state(request_id, "failed", {
                "session_id": session_id,
                "conversation_id": session_conversation_id,
                "error": str(api_error)
            })
            raise

        # Check for cancellation after API call
        if asyncio.current_task().cancelled():
            await set_request_state(request_id, "cancelled", {
                "session_id": session_id,
                "conversation_id": session_conversation_id
            })
            return session_conversation_id

        # Log the initial response to see if Claude is trying to use tools
        logger.info(f"Claude response stop_reason: {response.stop_reason}")
        if hasattr(response, 'content'):
            logger.info(f"Claude response content: {[{'type': getattr(block, 'type', 'unknown'), 'text': getattr(block, 'text', None)[:100] if hasattr(block, 'text') else None} for block in response.content]}")

        # Track the last tool_result timestamp for proper message ordering
        last_tool_result_timestamp = None

        # Handle tool calls
        while response.stop_reason == 'tool_use':
            # Check for cancellation before each tool iteration
            if asyncio.current_task().cancelled():
                logger.info(f"Request {request_id} cancelled during tool use")
                await set_request_state(request_id, "cancelled", {
                    "session_id": session_id,
                    "conversation_id": session_conversation_id
                })
                return session_conversation_id
            
            # Log all content blocks from AI response
            logger.info(f"AI response stop_reason: {response.stop_reason}")
            logger.info(f"AI response content blocks: {[{'type': block.type, 'name': getattr(block, 'name', None)} for block in response.content]}")
            
            tool_block = next((block for block in response.content if block.type == 'tool_use'), None)
            if not tool_block:
                logger.error("Stop reason is tool_use but no tool_use block found.")
                # Send some error or break, as this is an unexpected state
                error_payload = {
                    "error": "ServerError",
                    "detail": "Tool use indicated but no tool found.",
                    "request_id": request_id,
                    "client_conversation_id": client_conversation_id
                }
                await safe_websocket_send(error_payload)
                return

            logger.info(f"AI attempting to execute tool: {tool_block.name}")
            logger.info(f"Tool input parameters: {json.dumps(tool_block.input, indent=2)}")
            logger.debug(f"Executing tool: {tool_block.name} for user_id: {user_id} with input: {tool_block.input}")

            # Increment pending tool counter before execution
            await increment_pending_tools(session_conversation_id)

            # ===== CRITICAL: Save tool_use, TEXT, and PENDING tool_result IMMEDIATELY =====
            # This prevents race conditions where Claude sees incomplete conversation history
            # and triggers the same tool multiple times

            # Convert tool_block to a serializable dict
            tool_block_dict = {
                "type": "tool_use",
                "id": tool_block.id,
                "name": tool_block.name,
                "input": tool_block.input
            }

            # Extract text blocks from response, separating text BEFORE and AFTER tool_use
            # Text BEFORE tool_use should be saved with tool_use in Redis
            # Text AFTER tool_use should be saved separately (after tool execution completes)
            text_before_tool = []
            text_after_tool = []
            found_tool_use = False
            assistant_response_text_before = ""

            for block in response.content:
                if block.type == 'tool_use':
                    found_tool_use = True
                elif block.type == 'text':
                    text_dict = {"type": "text", "text": block.text}
                    if not found_tool_use:
                        # Text comes before tool_use
                        text_before_tool.append(text_dict)
                        if not assistant_response_text_before:
                            assistant_response_text_before = block.text
                    else:
                        # Text comes after tool_use - will be added to Redis later
                        text_after_tool.append(text_dict)

            # Calculate timestamps to ensure correct ordering in database
            # Text must come BEFORE tool_use, so we use explicit timestamps
            from datetime import datetime, timedelta

            # Get user message timestamp
            user_msg_result = supabase.table("messages")\
                .select("timestamp")\
                .eq("conversation_id", session_conversation_id)\
                .eq("request_id", request_id)\
                .eq("message_sender", "USER")\
                .execute()

            if user_msg_result.data:
                user_timestamp = user_msg_result.data[0]["timestamp"]
                # Parse and normalize timestamp
                timestamp_str = user_timestamp.replace('Z', '+00:00')
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

                user_dt = datetime.fromisoformat(timestamp_str)
                # Set timestamps: text_before (T+1ms), tool_use (T+2ms), tool_result (T+3ms), text_after (T+4ms)
                text_before_timestamp = (user_dt + timedelta(milliseconds=1)).isoformat().replace('+00:00', 'Z')
                tool_use_timestamp = (user_dt + timedelta(milliseconds=2)).isoformat().replace('+00:00', 'Z')
                tool_result_timestamp = (user_dt + timedelta(milliseconds=3)).isoformat().replace('+00:00', 'Z')
                text_after_timestamp = (user_dt + timedelta(milliseconds=4)).isoformat().replace('+00:00', 'Z')
                last_tool_result_timestamp = tool_result_timestamp  # Track for final message ordering
                logger.info(f"Using explicit timestamps: text_before={text_before_timestamp}, tool_use={tool_use_timestamp}, tool_result={tool_result_timestamp}, text_after={text_after_timestamp}")
            else:
                # Fallback: no explicit timestamps
                text_before_timestamp = None
                tool_use_timestamp = None
                tool_result_timestamp = None
                text_after_timestamp = None
                logger.warning(f"No USER message found with request_id {request_id}, using default timestamps")

            # Save assistant message to Redis BEFORE executing the tool
            # IMPORTANT: Only include text BEFORE tool_use, not after
            async with chat_sessions_lock:
                # Build assistant content: text_before + tool_use (NO text_after)
                assistant_content = text_before_tool + [tool_block_dict]
                await add_chat_message(session_id, {"role": "assistant", "content": assistant_content})

                # Save text response BEFORE tool to database FIRST if present (with earliest timestamp)
                # This ensures correct ordering when fetching from database
                if assistant_response_text_before:
                    text_save_result = save_message_to_db(
                        user_id=user_id,
                        conversation_id=session_conversation_id,
                        content=assistant_response_text_before,
                        message_sender="ASSISTANT",
                        request_id=request_id,
                        content_type="text",
                        client_timestamp=text_before_timestamp
                    )
                    if text_save_result:
                        logger.info(f"✅ Saved text message BEFORE tool to database: '{assistant_response_text_before[:50]}...'")
                    else:
                        logger.error(f"❌ Failed to save text message BEFORE tool to database")

                # Save tool_use to database SECOND (with timestamp after text)
                tool_use_save_result = save_message_to_db(
                    user_id=user_id,
                    conversation_id=session_conversation_id,
                    content="",  # Tool messages don't have text content
                    message_sender="ASSISTANT",
                    request_id=request_id,
                    content_type="tool_use",
                    tool_content=[tool_block_dict],
                    client_timestamp=tool_use_timestamp
                )
                logger.info(f"✅ Saved tool_use message to database BEFORE execution: {tool_block.name}")

                # Create PENDING tool result (will be updated with actual result later)
                pending_tool_result_dict = {
                    "type": "tool_result",
                    "tool_use_id": tool_block.id,
                    "content": json.dumps({"status": "pending", "message": "Tool execution in progress..."})
                }
                await add_chat_message(session_id, {"role": "user", "content": [pending_tool_result_dict]})

                # Save PENDING tool_result to database with timestamp after tool_use
                pending_result_save = save_message_to_db(
                    user_id=user_id,
                    conversation_id=session_conversation_id,
                    content="",  # Tool messages don't have text content
                    message_sender="USER",
                    request_id=request_id,
                    content_type="tool_result",
                    tool_content=[pending_tool_result_dict],
                    client_timestamp=tool_result_timestamp
                )
                logger.info(f"✅ Saved PENDING tool_result to database BEFORE execution: tool_use_id={tool_block.id}")

            # ===== Now execute the tool =====
            try:
                # Pass WebSocket context for tools that need it (like launch_app)
                tool_execution_result = await execute_tool(
                    tool_block.name,
                    tool_block.input,
                    user_id,
                    websocket=websocket,
                    tool_result_handler=tool_result_handler,
                    conversation_id=session_conversation_id
                )

                # Check if the tool_execution_result is our specific Asana auth error
                if isinstance(tool_execution_result, dict) and \
                   tool_execution_result.get("status_code") == 401 and \
                   "Asana authentication failed" in tool_execution_result.get("error", ""):
                    logger.info(f"Tool {tool_block.name} resulted in Asana auth error. Sending directly to client.")
                    # Add request_id and client context to Asana error response
                    tool_execution_result["request_id"] = request_id
                    tool_execution_result["client_conversation_id"] = client_conversation_id
                    await safe_websocket_send(tool_execution_result)
                    raise StopIteration("AsanaAuthErrorHandled") # Signal to skip normal response

                # The launch_app tool now handles WebSocket communication directly,
                # so we don't need special handling here anymore

                # ===== Update the PENDING tool_result with actual result =====
                async with chat_sessions_lock:
                    # Update Redis session with actual result
                    actual_tool_result_dict = {
                        "type": "tool_result",
                        "tool_use_id": tool_block.id,
                        "content": json.dumps(tool_execution_result)
                    }

                    # Replace the pending result in Redis
                    messages = await get_chat_messages(session_id)
                    for i in range(len(messages) - 1, -1, -1):  # Search backwards for efficiency
                        msg = messages[i]
                        if msg.get("role") == "user" and isinstance(msg.get("content"), list):
                            for block in msg["content"]:
                                if isinstance(block, dict) and \
                                   block.get("type") == "tool_result" and \
                                   block.get("tool_use_id") == tool_block.id:
                                    # Found the pending result, update it
                                    messages[i] = {"role": "user", "content": [actual_tool_result_dict]}
                                    await set_chat_messages(session_id, messages)
                                    logger.info(f"✅ Updated Redis with actual tool_result for tool_use_id={tool_block.id}")
                                    break

                    # Update database with actual result
                    update_success = update_tool_result_in_db(
                        conversation_id=session_conversation_id,
                        tool_use_id=tool_block.id,
                        result_content=tool_execution_result
                    )
                    if update_success:
                        logger.info(f"✅ Updated database with actual tool_result for tool: {tool_block.name}")
                    else:
                        logger.error(f"❌ Failed to update database with actual tool_result for tool: {tool_block.name}")

                # Save text AFTER tool_use to both Redis and database (if any)
                # This text should come AFTER the tool_result in the conversation
                if text_after_tool:
                    # Add to Redis
                    await add_chat_message(session_id, {"role": "assistant", "content": text_after_tool})
                    logger.info(f"✅ Added text AFTER tool to Redis session")

                    # Save to database with timestamp after tool_result
                    text_after_content = " ".join([block["text"] for block in text_after_tool if block.get("type") == "text"])
                    if text_after_content:
                        text_after_save_result = save_message_to_db(
                            user_id=user_id,
                            conversation_id=session_conversation_id,
                            content=text_after_content,
                            message_sender="ASSISTANT",
                            request_id=request_id,
                            content_type="text",
                            client_timestamp=text_after_timestamp
                        )
                        if text_after_save_result:
                            logger.info(f"✅ Saved text AFTER tool to database: '{text_after_content[:50]}...'")
                        else:
                            logger.error(f"❌ Failed to save text AFTER tool to database")
            finally:
                # Always decrement counter, even if tool execution or saving fails
                await decrement_pending_tools(session_conversation_id)
            
            # Use async API call for tool response
            try:
                # Tool responses should not stream (we explicitly pass stream=False)
                tool_coroutine = await call_claude_api(
                    current_anthropic_client, 
                    session_id, 
                    stream=False,  # Explicitly disable streaming for tool responses
                    conversation_id=session_conversation_id
                )
                
                # Create a task for cancellability (fixes "cannot reuse coroutine" error)
                tool_task = asyncio.create_task(tool_coroutine)
                
                # Note: We could store the task for cancellation if needed
                # For now, just await it with timeout
                response = await asyncio.wait_for(tool_task, timeout=60.0)  # 60 second timeout for tool responses too
            except asyncio.TimeoutError:
                logger.error(f"Claude API timeout during tool use for request {request_id}")
                await set_request_state(request_id, "timeout", {
                    "session_id": session_id,
                    "conversation_id": session_conversation_id,
                    "error": "Claude API timed out during tool use"
                })
                raise
            except AuthenticationError as auth_error:
                # Handle Claude API authentication errors during tool use
                logger.warning(f"Claude API authentication error during tool use for request {request_id}: {str(auth_error)}")
                await set_request_state(request_id, "auth_failed", {
                    "session_id": session_id,
                    "conversation_id": session_conversation_id,
                    "error": str(auth_error)
                })
                
                # Create error payload
                error_payload = {
                    "type": "error",
                    "code": "CLAUDE_AUTHENTICATION_ERROR",
                    "message": f"Claude API authentication failed: {str(auth_error)}. Please check your Claude API Key in settings.",
                    "request_id": request_id,
                    "conversation_id": session_conversation_id,
                    "client_conversation_id": client_conversation_id
                }
                
                # Save error as ASSISTANT message to database (persists even if WebSocket fails)
                error_json_content = json.dumps(error_payload)
                logger.info(f"Saving authentication error (during tool use) as ASSISTANT message for conversation {session_conversation_id}")
                save_result = save_message_to_db(
                    user_id=user_id,
                    conversation_id=session_conversation_id,
                    content=error_json_content,
                    message_sender="ASSISTANT",
                    request_id=request_id,
                    content_type="text"
                )
                if save_result:
                    saved_conversation_id, error_message_id = save_result
                    logger.info(f"Authentication error saved as message {error_message_id} in conversation {saved_conversation_id}")
                else:
                    logger.error(f"Failed to save authentication error message to database")
                
                # Try to send via WebSocket (may fail, but error is persisted in DB)
                await safe_websocket_send(error_payload)
                
                # Don't raise - let the client handle the error gracefully
                return session_conversation_id
        
        # Log final response details
        logger.info(f"Final AI response - stop_reason: {response.stop_reason}")
        logger.info(f"Final AI response - content blocks: {[{'type': block.type, 'text': getattr(block, 'text', '')[:100] if hasattr(block, 'text') else None} for block in response.content]}")
        
        # Add assistant final response to session history (if not an intercepted error)
        # IMPORTANT: Only add text blocks here, NOT tool_use blocks
        # Tool_use blocks were already added in the tool loop above
        async with chat_sessions_lock:
            # Convert response.content to serializable format - ONLY text blocks
            content_list = []
            for block in response.content:
                if block.type == 'text':
                    content_list.append({"type": "text", "text": block.text})
                # Skip tool_use blocks - they were already added in the tool loop

            # Only add message if there's text content
            if content_list:
                await add_chat_message(session_id, {"role": "assistant", "content": content_list})
        
        # Extract and save assistant response to database
        # Look for text in ANY content block, not just the first one
        assistant_response_text = ""
        if response.content:
            for block in response.content:
                if block.type == 'text':
                    assistant_response_text = block.text
                    break

        # Check if request was cancelled
        request_cancelled = False
        if redis_managers and "request_messages" in redis_managers:
            request_data = await redis_managers["request_messages"].get_messages(request_id)
            if request_data and request_data.get("status") == "cancelled":
                request_cancelled = True
                logger.info(f"Request {request_id} was cancelled, will save bot message as cancelled")

        # Save assistant message to database if there's text content (always save, but mark as cancelled if needed)
        saved_conversation_id = session_conversation_id
        save_result = None
        if assistant_response_text:
            logger.info(f"About to save ASSISTANT message: conversation_id={session_conversation_id}, request_id={request_id}, cancelled={request_cancelled}, content_preview='{assistant_response_text[:50]}...'")

            # If this is a response after tool execution, ensure it has a timestamp AFTER the tool_result
            # to maintain proper message ordering when loading from database
            final_text_timestamp = None
            if last_tool_result_timestamp:  # last_tool_result_timestamp was set in the tool loop
                # Parse tool_result timestamp and add 1ms to ensure final text comes after
                try:
                    from datetime import datetime, timedelta
                    tool_result_dt = datetime.fromisoformat(last_tool_result_timestamp.replace('Z', '+00:00'))
                    final_text_timestamp = (tool_result_dt + timedelta(milliseconds=1)).isoformat().replace('+00:00', 'Z')
                    logger.info(f"Setting final assistant text timestamp to {final_text_timestamp} (after tool_result)")
                except:
                    pass

            save_result = save_message_to_db(user_id, session_conversation_id, assistant_response_text, "ASSISTANT", request_id, content_type="text", client_timestamp=final_text_timestamp)
            if save_result:
                saved_conversation_id, bot_message_id = save_result
                logger.info(f"ASSISTANT message saved successfully: conversation_id={saved_conversation_id}, message_id={bot_message_id}")

                # If cancelled, update the message to mark it as cancelled
                if request_cancelled:
                    try:
                        supabase.table("messages")\
                            .update({"cancelled": "now()"})\
                            .eq("id", bot_message_id)\
                            .execute()
                        logger.info(f"Marked bot message {bot_message_id} as cancelled in database")
                    except Exception as e:
                        logger.error(f"Failed to mark bot message {bot_message_id} as cancelled: {e}")
            else:
                logger.error(f"Failed to save ASSISTANT message for conversation_id={session_conversation_id}, request_id={request_id}")
        else:
            logger.info(f"No text content in Claude's response for request {request_id}, only tool calls. This is normal for tool-only responses.")

        if not request_cancelled:
            # Send structured response with request_id and conversation_id
            # Even if assistant_response_text is empty, we send the response to acknowledge completion
            response_payload = {
                "response": assistant_response_text,
                "request_id": request_id,
                "conversation_id": session_conversation_id,  # Include real conversation_id for client sync
                "client_conversation_id": client_conversation_id,  # Echo back optimistic ID
                "client_message_id": client_message_id,  # Echo back optimistic message ID
                "type": "response"  # Indicate this is a direct response (vs broadcast)
            }
            if await safe_websocket_send(response_payload):
                logger.info(f"Successfully sent response for request {request_id} to session {session_id}")
            else:
                logger.info(f"WebSocket send failed but response saved to database: conversation_id={saved_conversation_id}, request_id={request_id}, will be available on reconnect")

        # Clean up old request tracking data
        # Do this after saving the bot response to ensure we have the latest message IDs
        if save_result and redis_managers and "request_messages" in redis_managers:
            try:
                # Get all non-cancelled bot message IDs
                bot_message_ids = get_non_cancelled_bot_message_ids(saved_conversation_id)

                # Clean up requests that have 2+ bot messages after them
                cleaned_count = await redis_managers["request_messages"].cleanup_old_requests(
                    saved_conversation_id, bot_message_ids
                )

                if cleaned_count > 0:
                    logger.info(f"Cleaned up {cleaned_count} old request tracking entries for conversation {saved_conversation_id}")
            except Exception as e:
                logger.warning(f"Error cleaning up old request tracking: {e}")

        # Mark request as completed successfully
        await set_request_state(request_id, "completed", {
            "session_id": session_id,
            "conversation_id": session_conversation_id,
            "response_sent": True,
            "completion_time": time.time()
        })

        # Broadcast to other WebSocket sessions for the same conversation
        # Use same format as regular response so clients can process it correctly
        broadcast_payload = {
            "response": assistant_response_text,
            "request_id": request_id,  # Include request_id so clients can track the message
            "conversation_id": session_conversation_id,
            "client_conversation_id": client_conversation_id,  # Include for client validation
            "client_message_id": client_message_id,  # Include for completeness
            "type": "broadcast"  # Keep type to indicate it's a broadcast
        }
        # Bot responses should go to ALL sessions - they originate from the server, not from any client session
        await broadcast_to_conversation(session_conversation_id, broadcast_payload, exclude_session=None)
        logger.info(f"Broadcasted assistant message to all sessions for conversation {session_conversation_id}")

        return session_conversation_id
    
    except asyncio.CancelledError:
        # Handle task cancellation gracefully
        # The task was cancelled before reaching Claude (interrupt detected early)
        logger.info(f"Request {request_id} was cancelled (task cancellation)")
        await set_request_state(request_id, "cancelled", {
            "session_id": session_id,
            "conversation_id": session_conversation_id,
            "cancelled_at": time.time()
        })
        # Don't re-raise - let the finally block run for cleanup
        return session_conversation_id
    
    except Exception as e:
        # Track any other errors
        logger.error(f"Error processing request {request_id}: {str(e)}")
        await set_request_state(request_id, "failed", {
            "session_id": session_id,
            "conversation_id": session_conversation_id,
            "error": str(e),
            "error_time": time.time()
        })
        raise
        
    finally:
        # Clean up tracking
        if request_id:
            # Remove Claude stream from tracking
            if redis_managers and "local_objects" in redis_managers:
                await redis_managers["local_objects"].remove_stream(request_id)
            
            # Remove from local task tracking
            if redis_managers and "local_objects" in redis_managers:
                await redis_managers["local_objects"].remove_task(request_id)
            
            # Remove from active requests (both local and Redis)
            await remove_active_request(session_id, request_id)
            logger.debug(f"Removed request {request_id} from active requests for session {session_id}")
        
        # Clean up chat session if WebSocket is disconnected and no more active tasks
        # This ensures we don't leak memory from disconnected sessions
        try:
            # Check if WebSocket is still connected
            websocket_connected = False
            try:
                # Try to check WebSocket state without sending anything
                # WebSocket.client_state tells us if it's connected
                websocket_connected = websocket.client_state.name == "CONNECTED"
            except:
                websocket_connected = False
            
            # If disconnected and no more active tasks for this session, clean up chat history
            if not websocket_connected:
                active_reqs = await get_active_requests(session_id)
                session_has_active_requests = bool(active_reqs)
                
                if not session_has_active_requests:
                    # Check if session has messages before clearing
                    messages = await get_chat_messages(session_id)
                    if messages:
                        await clear_chat_session(session_id)
                        logger.info(f"Cleaned up chat session {session_id} after task completion (WebSocket disconnected)")
        except Exception as e:
            logger.warning(f"Error during post-task cleanup for session {session_id}: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)