from fastapi import FastAPI, WebSocket, HTTPException, WebSocketDisconnect, Depends, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials, HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel
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
from asana_tools import asana_tools, get_asana_tasks, get_asana_workspaces, get_current_date, get_parent_tasks, create_asana_task, change_task_parent, update_task_due_date
from about_me_tool import about_me_tools, get_app_info
from preferences import set_preference, get_preference, ensure_user_and_prefs, get_decrypted_preference_key, set_encrypted_preference_key, CLAUDE_API_KEY_PREF_NAME, set_user_timezone
from auth import verify_google_token, create_access_token, get_current_user, AuthError, SECRET_KEY as AUTH_SECRET_KEY, ALGORITHM as AUTH_ALGORITHM, create_refresh_token
from supabase_client import supabase
from redis_managers import create_managers
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

# System prompt for Claude
CLAUDE_SYSTEM_PROMPT = "You are Whiz Voice, a friendly AI chatbot that can help with anything. If the user mentions Asana or tasks, please use the tools provided to answer the user's question, using multiple tools at once if necessary. Also, you have a get_app_info tool that can be used to get information about the Whiz Voice app, including its features, functionality, and how to use it. Note that you are a voice app, so please keep your responses brief so that they don't take too long to be read out loud."

# can concatenate additional tools here if needed
tools = asana_tools + about_me_tools

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
    logger.info(f"Headers: {dict(request.headers)}")
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

# Middleware to log request headers
@app.middleware("http")
async def log_request_headers(request: Request, call_next):
    # Log all headers
    header_log_str = f"Incoming request to {request.url.path} with headers:\n"
    for name, value in request.headers.items():
        header_log_str += f"  {name}: {value}\n"
    logger.info(header_log_str)
    
    response = await call_next(request)
    return response

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

# Local-only data structures moved to LocalObjectManager in redis_managers

# Legacy local dictionaries - to be replaced by Redis managers
# Keeping temporarily for backwards compatibility during migration
chat_sessions = {}  # DEPRECATED - use redis_managers["chat_sessions"]
user_sessions = {}  # DEPRECATED - use redis_managers["user_sessions"]
session_timestamps: Dict[str, float] = {}  # DEPRECATED - use redis_managers["session_timestamps"]
request_states: Dict[str, Dict[str, Any]] = {}  # Track request states locally as fallback

# Define the response model for the new GET endpoint
ASANA_ACCESS_TOKEN_PREF_NAME = "asana_access_token" # Define this constant

# Locks for thread-safe access to shared dictionaries
chat_sessions_lock = asyncio.Lock()
user_sessions_lock = asyncio.Lock()
session_timestamps_lock = asyncio.Lock()
anthropic_clients_cache_lock = asyncio.Lock()
request_states_lock = asyncio.Lock()


async def migrate_local_to_redis():
    """Migrate existing local session data to Redis (one-time during startup)"""
    if not redis_managers:
        return
    
    try:
        # Migrate chat sessions
        for session_id, messages in chat_sessions.items():
            await redis_managers["chat_sessions"].set(session_id, messages)
        
        # Migrate user sessions
        for user_id, session_ids in user_sessions.items():
            for session_id in session_ids:
                await redis_managers["user_sessions"].add_session(user_id, session_id)
        
        # Migrate session timestamps
        for session_id, timestamp in session_timestamps.items():
            await redis_managers["session_timestamps"].update(session_id, timestamp)
        
        # Active requests are now fully managed by Redis - no migration needed
        
        # Session mappings migration no longer needed - removed deprecated local storage
        
        if chat_sessions or user_sessions or session_timestamps:
            logger.info(f"Migrated local data to Redis: {len(chat_sessions)} chat sessions, "
                       f"{len(user_sessions)} user sessions, {len(session_timestamps)} timestamps")
    except Exception as e:
        logger.error(f"Failed to migrate local data to Redis: {e}")


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
        
        # Initialize the helper module with managers and local storage
        local_storage = {
            "chat_sessions": chat_sessions,
            "user_sessions": user_sessions,
            "session_timestamps": session_timestamps,
            "request_states": request_states
        }
        locks = {
            "chat_sessions_lock": chat_sessions_lock,
            "user_sessions_lock": user_sessions_lock,
            "session_timestamps_lock": session_timestamps_lock,
            "request_states_lock": request_states_lock
        }
        set_managers_and_storage(redis_managers, local_storage, locks)
        
        # Migrate any existing local data to Redis (for smooth transition)
        await migrate_local_to_redis()
        
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
        
        logger.info(f"Session {session_id} subscribed to Redis channel {channel_name}")
        
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
            await redis_managers["local_objects"].update_conversation_websocket(
                session_id, old_conversation_id, new_conversation_id, websocket
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
                    await update_session_activity(session_id)
                    
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
    "get_app_info": {
        "function_name": "get_app_info",
        "requires_auth": False,
        "args_mapping": lambda args, user_id: (user_id,),
        "validation": None
    }
}

def execute_tool(tool_name, tool_args, user_id: Optional[str] = None):
    """Execute a tool using the tool registry"""
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
        func_args = tool_config["args_mapping"](tool_args, user_id)
        
        # Get the actual function using globals() for easy mocking
        function_name = tool_config["function_name"]
        if function_name in globals():
            func = globals()[function_name]
        else:
            raise ValueError(f"Function {function_name} not found")
            
        # Call the function with the mapped arguments
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
        # CLAUDE_API_KEY_PREF_NAME should be imported from preferences or defined globally
        claude_key = get_decrypted_preference_key(user_id, CLAUDE_API_KEY_PREF_NAME)
        asana_key = get_decrypted_preference_key(user_id, ASANA_ACCESS_TOKEN_PREF_NAME)

        has_claude = bool(claude_key) 
        has_asana = bool(asana_key)   
        
        logger.info(f"Checked token status for user {user_id}. Claude: {has_claude}, Asana: {has_asana}")
        
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
    request: UserApiKeySetRequest,
    current_user: Dict = Depends(get_current_user) # Ensures endpoint is protected
):
    user_id = current_user.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="User not authenticated")

    if request.key_name not in ALLOWED_API_KEY_NAMES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid key_name: '{request.key_name}'. Allowed keys are: {list(ALLOWED_API_KEY_NAMES)}"
        )
    
    # Allowing request.key_value to be None or an empty string to clear the key.
    # set_encrypted_preference_key should handle this by storing None or empty string,
    # which should then be retrievable as such.
    if set_encrypted_preference_key(user_id, request.key_name, request.key_value):
        logger.info(f"Successfully set preference key '{request.key_name}' for user {user_id}.")
        return {"message": f"Successfully set API key: '{request.key_name}'"}
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

        # Create a new access token - payload might need more than just sub for access tokens
        # Re-fetch user details or ensure access token payload is consistent
        # For simplicity, if access_token_data in /auth/google was just {"sub": user_id, "email": ..., "name": ...}
        # we need to ensure that info is still available or decide what goes into a refreshed access token.
        # Let's assume for now the access token only strictly needs 'sub' for get_current_user, 
        # but it's better if it matches the original structure.
        # Since we don't have email/name from refresh token, we keep new access token minimal.
        new_access_token_data = {
            "sub": user_id,
            # If you need email/name in access tokens and they aren't in refresh token,
            # you might need to fetch them from DB or adjust what `get_current_user` relies on.
            # For now, this will make the access token a bit simpler than the login one.
        }
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
        # Debug log all query parameters
        logger.info(f"WebSocket query params: {dict(websocket.query_params)}")
        if "conversation_id" in websocket.query_params:
            try:
                conversation_id = int(websocket.query_params["conversation_id"])
                logger.info(f"WebSocket connection requested for conversation_id={conversation_id}")
            except ValueError:
                logger.warning(f"Invalid conversation_id parameter: {websocket.query_params['conversation_id']}")
        
        # Authenticate if token is present
        user_id = None
        if token:
            try:
                # Verify token using our server's algorithm (HS256)
                from jose import jwt, JWTError
                
                logger.debug(f"WebSocket attempting to verify token (first 15 chars): {token[:15]}...")
                payload = jwt.decode(token, AUTH_SECRET_KEY, algorithms=[AUTH_ALGORITHM])
                user_id = payload.get("sub")
                user_email = payload.get("email")
                user_name = payload.get("name", "there")
                
                logger.info(f"Authenticated WebSocket connection for user {user_email} ({user_id})")
                
                # Check global session limit before creating new session
                async with chat_sessions_lock:
                    total_sessions = len(chat_sessions)
                
                # Reject if at capacity
                if total_sessions >= MAX_TOTAL_SESSIONS:
                    logger.error(f"MAX_TOTAL_SESSIONS reached: {total_sessions}/{MAX_TOTAL_SESSIONS}. Rejecting connection from {user_email}")
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
                
                # Use actual_conversation_id for loading history (it already handles optimistic IDs internally,
                # but we've already resolved it so we can pass the real ID directly)
                conversation_history = load_conversation_history(user_id, actual_conversation_id)
                
                await set_chat_messages(session_id, conversation_history)
                
                logger.info(f"Created session {session_id} with {len(conversation_history)} messages")
                
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
                
                # Don't send welcome message for new chats - let the UI show placeholder text instead
                if conversation_history:
                    logger.info(f"Loaded {len(conversation_history)} messages from conversation history for user {user_id}")
                else:
                    logger.info(f"New chat session - no conversation history, UI will show placeholder text")
                
                # Log the resolution if it happened
                if conversation_id != actual_conversation_id:
                    logger.info(f"WebSocket connected with optimistic ID {conversation_id}, resolved to real ID {actual_conversation_id}")
                    
            except JWTError as e:
                logger.warning(f"WebSocket JWTError: {str(e)}. Closing connection.")
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
        # session_id = f"ws_{user_id}"  # Already created above
        # chat_sessions[session_id] = []  # Already initialized above with conversation history
        
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
                        has_active_requests = False
                        active_request_ids = []
                        if redis_managers and "active_requests" in redis_managers:
                            active_request_ids = list(await redis_managers["active_requests"].get_all(session_id))
                            has_active_requests = len(active_request_ids) > 0
                            if has_active_requests:
                                # Clear all active requests for this session in Redis
                                await redis_managers["active_requests"].clear(session_id)
                        
                        if has_active_requests:
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
                            # Cancel all active requests for this session
                            if redis_managers and "local_objects" in redis_managers:
                                cancelled_count = await redis_managers["local_objects"].cancel_tasks_by_ids(list(active_request_ids))
                                logger.info(f"Cancelled {cancelled_count} tasks")
                            
                            # Send interrupt notification with client context
                            interrupt_response = {
                                "type": "interrupted", 
                                "message": "Previous request cancelled due to new message",
                                "request_id": request_id,
                                "client_conversation_id": client_conversation_id
                            }
                            await websocket.send_text(json.dumps(interrupt_response))
                        
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
                        
                        # Wait for task completion and update session_conversation_id
                        try:
                            updated_session_conversation_id = await task
                            if updated_session_conversation_id is not None:
                                # Register WebSocket with new conversation ID if it changed
                                if updated_session_conversation_id != session_conversation_id:
                                    # Use the safe update function that creates new listener before destroying old one
                                    try:
                                        await update_websocket_conversation(session_id, session_conversation_id, updated_session_conversation_id, websocket)
                                    except Exception as e:
                                        logger.error(f"Failed to update WebSocket conversation in main loop: {str(e)}")
                                        # Continue with the current conversation ID if update fails
                                
                                session_conversation_id = updated_session_conversation_id
                        except asyncio.CancelledError:
                            logger.info(f"Request {request_id} was cancelled")
                            # Clean up tracking
                            if request_id and redis_managers:
                                if "local_objects" in redis_managers:
                                    await redis_managers["local_objects"].remove_task(request_id)
                                if "active_requests" in redis_managers:
                                    await redis_managers["active_requests"].remove(session_id, request_id)
                        except Exception as e:
                            logger.error(f"Error processing message: {e}")
                            # Clean up tracking
                            if request_id and redis_managers:
                                if "local_objects" in redis_managers:
                                    await redis_managers["local_objects"].remove_task(request_id)
                                if "active_requests" in redis_managers:
                                    await redis_managers["active_requests"].remove(session_id, request_id)
                            raise

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

async def update_session_activity(session_id: str) -> None:
    """Helper function to update session activity timestamp - wrapper for Redis version"""
    await update_session_activity_redis(session_id)
    logger.debug(f"Updated activity for session {session_id}")

async def broadcast_to_conversation(conversation_id: int, message_payload: dict, exclude_session: Optional[str] = None):
    """Broadcast a message to all WebSocket connections for a specific conversation"""
    
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
            
            # ALSO publish to any optimistic conversation ID channels
            # Look up if this real ID has an associated optimistic ID
            try:
                opt_result = supabase.table("conversations")\
                    .select("optimistic_chat_id")\
                    .eq("id", conversation_id)\
                    .execute()
                
                if opt_result.data and opt_result.data[0].get("optimistic_chat_id"):
                    optimistic_id = opt_result.data[0]["optimistic_chat_id"]
                    opt_channel_name = f"conversation:{optimistic_id}"
                    await redis_client.publish(opt_channel_name, json.dumps(message_data))
                    logger.info(f"Also published message to optimistic Redis channel {opt_channel_name}")
            except Exception as opt_e:
                logger.warning(f"Could not check for optimistic ID for conversation {conversation_id}: {str(opt_e)}")
        except Exception as e:
            logger.error(f"Failed to publish to Redis: {str(e)}")
    
    # Also use local broadcasting for WebSockets in this process
    connections = []
    
    if redis_managers and "local_objects" in redis_managers:
        # Get connections for the real conversation ID
        connections.extend(await redis_managers["local_objects"].get_conversation_websockets(conversation_id))
        
        # ALSO check for any optimistic conversation IDs that map to this real ID
        # First check the database for the optimistic ID
        try:
            opt_result = supabase.table("conversations")\
                .select("optimistic_chat_id")\
                .eq("id", conversation_id)\
                .execute()
            
            if opt_result.data and opt_result.data[0].get("optimistic_chat_id"):
                optimistic_id = int(opt_result.data[0]["optimistic_chat_id"])
                opt_connections = await redis_managers["local_objects"].get_conversation_websockets(optimistic_id)
                if opt_connections:
                    logger.info(f"Found WebSockets registered under optimistic ID {optimistic_id} for real conversation {conversation_id}")
                    connections.extend(opt_connections)
        except Exception as e:
            logger.warning(f"Could not check for optimistic ID in database: {str(e)}")
        
        # Also check Redis session_mappings as a fallback
        # Note: This requires iterating through all sessions since Redis doesn't have reverse mapping lookup
        # In practice, this fallback should rarely be needed since the database stores optimistic IDs
        if redis_managers and "session_mappings" in redis_managers:
            # For now, log that we're skipping this check since it would be inefficient
            # The database check above should handle most cases
            logger.debug(f"Skipping exhaustive session_mappings check for conversation {conversation_id}")
    
    if not connections:
        logger.info(f"No local WebSocket sessions registered for conversation {conversation_id} (checked real and optimistic IDs)")
        return
    
    # Create a copy of the list to avoid modification during iteration
    connections = list(set(connections))  # Remove duplicates
    
    logger.info(f"Broadcasting locally to conversation {conversation_id} with {len(connections)} registered sessions: {[sid for sid, _ in connections]}")
    disconnected_sessions = []
    
    for session_id, websocket in connections:
        # Skip the session that originated the message (if specified)
        if exclude_session and session_id == exclude_session:
            logger.info(f"Skipping originating session {session_id}")
            continue
            
        try:
            # Update activity for session receiving broadcast
            await update_session_activity(session_id)
            await websocket.send_text(json.dumps(message_payload))
            logger.info(f"Broadcasted message locally to session {session_id} for conversation {conversation_id}")
        except Exception as e:
            logger.warning(f"Failed to broadcast locally to session {session_id}: {str(e)}")
            disconnected_sessions.append((session_id, websocket))
    
    # Clean up disconnected sessions
    if disconnected_sessions and redis_managers and "local_objects" in redis_managers:
        for session_id, websocket in disconnected_sessions:
            await redis_managers["local_objects"].remove_websocket_from_conversation(
                conversation_id, session_id, websocket
            )

async def cleanup_session(session_id: str, user_id: Optional[str] = None, conversation_id: Optional[int] = None):
    """Clean up a session when a WebSocket disconnects"""
    # Don't delete chat_sessions immediately - let any active message processing tasks complete
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
            logger.info(f"Removing dead session {dead_sess} from user_sessions during eviction check")
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
        await cleanup_session(eviction_candidate, user_id, evicted_conversation_id)

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
            logger.info(f"Loading specified conversation {actual_conversation_id} for user {user_id}")
        
        # Get messages for the conversation
        result = supabase.table("messages").select("*").eq("conversation_id", actual_conversation_id).order("timestamp", desc=False).execute()
        
        # Convert database messages to Claude format
        claude_messages = []
        for row in result.data:
            message_role = "user" if row["message_type"] == "USER" else "assistant"
            claude_messages.append({
                "role": message_role,
                "content": row["content"]
            })
        
        logger.info(f"Loaded {len(claude_messages)} messages from conversation {actual_conversation_id} for user {user_id}")
        return claude_messages
        
    except Exception as e:
        logger.error(f"Error loading conversation history for user {user_id}, conversation {conversation_id}: {str(e)}")
        return []

def save_message_to_db(user_id: str, conversation_id: Optional[int], content: str, message_type: str, request_id: Optional[str] = None, client_conversation_id: Optional[int] = None, client_timestamp: Optional[str] = None) -> Optional[int]:
    """Save a message to the database and return the conversation_id"""
    try:
        logger.info(f"save_message_to_db called: user_id={user_id}, conversation_id={conversation_id}, message_type={message_type}, client_conversation_id={client_conversation_id}, content='{content[:50]}...'")
        
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
            logger.info(f"Using existing conversation {conversation_id} for user {user_id}")
        
        # Save the message
        logger.info(f"Attempting to save {message_type} message to conversation_id={conversation_id}, request_id={request_id}")
        
        # Prepare message data
        message_data = {
            "conversation_id": conversation_id,
            "content": content,
            "message_type": message_type,
            "request_id": request_id
        }
        
        # For USER messages with client_timestamp, use the provided timestamp to preserve message order
        if message_type == "USER" and client_timestamp:
            # Client timestamp is already in ISO format from Android client
            message_data["timestamp"] = client_timestamp
            logger.info(f"Using client-provided timestamp for USER message: {client_timestamp}")
        
        # For ASSISTANT messages with request_id, set timestamp to be right after the USER message
        # This ensures the response appears immediately after the user message it's responding to
        if message_type == "ASSISTANT" and request_id:
            # Find the USER message with this request_id
            user_msg_result = supabase.table("messages")\
                .select("timestamp")\
                .eq("conversation_id", conversation_id)\
                .eq("request_id", request_id)\
                .eq("message_type", "USER")\
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
        logger.info(f"Successfully saved {message_type} message: message_id={message_id}, conversation_id={saved_conv_id}, request_id={request_id}")
        
        # Update conversation last_message_time and updated_at for incremental sync
        update_result = supabase.table("conversations").update({
            "last_message_time": "now()",
            "updated_at": "now()"  # Critical: update this so incremental sync catches new messages
        }).eq("id", conversation_id).execute()
        
        if update_result.data:
            logger.info(f"Updated conversation {conversation_id} timestamps for {message_type} message")
        else:
            logger.warning(f"Failed to update conversation {conversation_id} timestamps")
        
        return conversation_id
        
    except Exception as e:
        logger.error(f"Error saving message to database: {str(e)}")
        return None

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
        
        # Get both tokens
        claude_token = get_decrypted_preference_key(user_id, 'claude_api_key')
        asana_token = get_decrypted_preference_key(user_id, 'asana_access_token')
        
        return {
            "has_claude_token": claude_token is not None,
            "has_asana_token": asana_token is not None
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
        logger.info(f"Successfully set timezone for user {user_id} via API: {request.timezone}")
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
        # Delete all conversations for user (messages will cascade delete)
        result = supabase.table("conversations").delete().eq("user_id", user_id).execute()
        
        return {"message": "All conversations deleted successfully"}
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
    since: Optional[str] = None,  # ISO timestamp string for incremental sync
    limit: Optional[int] = 100,   # Pagination support
    current_user: Dict = Depends(get_current_user)
):
    """Get messages for a conversation with optional incremental sync"""
    try:
        user_id = current_user.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="User not authenticated")
        
        logger.info(f"Getting messages for conversation {conversation_id}, user {user_id}, since: {since}")
        
        # Resolve the actual conversation ID (handles optimistic IDs)
        actual_conversation_id = resolve_conversation_id(conversation_id, user_id)
        if actual_conversation_id is None:
            logger.warning(f"No conversation found for conversation ID {conversation_id}")
            raise HTTPException(status_code=404, detail="Conversation not found")
        
        # Build the messages query
        query = supabase.table("messages")\
            .select("*")\
            .eq("conversation_id", actual_conversation_id)\
            .order("updated_at", desc=False)\
            .limit(limit)
        
        # Add incremental sync filter if provided
        if since:
            try:
                # Parse the timestamp and filter for records updated after it
                since_timestamp = datetime.fromisoformat(since.replace('Z', '+00:00'))
                query = query.gte('updated_at', since_timestamp.isoformat())
                logger.info(f"Incremental sync: fetching messages updated since {since_timestamp}")
            except ValueError as e:
                logger.warning(f"Invalid 'since' timestamp format: {since}, error: {e}")
                # Fall back to full sync if timestamp is invalid
        
        response = query.execute()
        messages = response.data if response.data else []
        
        # Update conversation_id in messages to use the actual server-backed ID
        # This ensures clients always receive messages with positive server-backed IDs
        for message in messages:
            message['conversation_id'] = actual_conversation_id
            # Debug logging to diagnose message_type issue and timestamp
            logger.info(f"Message ID {message.get('id')}: type={message.get('message_type')}, request_id={message.get('request_id')}, timestamp={message.get('timestamp')}")
        
        # Return with server timestamp for next incremental sync
        result = {
            'messages': messages,
            'conversation_id': actual_conversation_id,  # Return the resolved server-backed ID, not the parameter
            'server_timestamp': datetime.utcnow().isoformat() + 'Z',
            'is_incremental': since is not None,
            'count': len(messages),
            'has_more': len(messages) == limit  # Indicates if there might be more messages
        }
        
        logger.info(f"Returning {len(messages)} messages for conversation {conversation_id} (incremental: {since is not None})")
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
            "message_type": message.message_type,
            "request_id": message.request_id
        }
        
        # Include timestamp if provided to preserve message order
        if message.timestamp:
            message_data["timestamp"] = message.timestamp
            logger.info(f"Using client-provided timestamp for message: {message.timestamp}")
        
        result = supabase.table("messages").insert(message_data).execute()
        
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
            message_type=row["message_type"],
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

@app.post("/conversations/{conversation_id}/sync")
async def sync_missed_messages(
    conversation_id: Union[int, str],
    since_timestamp: Optional[float] = None,
    current_user: dict = Depends(get_current_user)
):
    """Sync any messages that may have been missed during disconnection"""
    try:
        user_id = current_user.get("sub")
        
        # Resolve conversation ID
        actual_conversation_id = resolve_conversation_id(conversation_id, user_id)
        if actual_conversation_id is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        
        # Get messages from database
        query = supabase.table("messages").select("*").eq("conversation_id", actual_conversation_id)
        
        if since_timestamp:
            # Convert timestamp to ISO format for Supabase
            from datetime import datetime
            since_datetime = datetime.fromtimestamp(since_timestamp).isoformat()
            query = query.gt("created_at", since_datetime)
        
        result = query.order("created_at", desc=False).execute()
        
        return {
            "conversation_id": actual_conversation_id,
            "messages": result.data,
            "count": len(result.data)
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error syncing messages for conversation {conversation_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to sync messages")

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
            await websocket.send_text(json.dumps(payload))
            return True
        except Exception as e:
            logger.warning(f"WebSocket send failed for session {session_id}: {str(e)} - Response will be available on reconnect")
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
        logger.info(f"Processing message in session {session_id}, conversation {session_conversation_id}, context length: {len(messages)}")
        
        # Save user message to database and update session_conversation_id
        logger.info(f"About to save user message. Current session_conversation_id: {session_conversation_id}")
        real_conversation_id = save_message_to_db(user_id, session_conversation_id, message, "USER", request_id, client_conversation_id, client_timestamp)
        logger.info(f"After saving user message. Updated session_conversation_id: {real_conversation_id}")
        if real_conversation_id is None:
            logger.error("Failed to save user message to database")
            error_payload = {
                "type": "error",
                "code": "DATABASE_ERROR",
                "message": "Failed to save message to database",
                "request_id": request_id,
                "client_conversation_id": client_conversation_id
            }
            await safe_websocket_send(error_payload)
            return real_conversation_id
        
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

        async with chat_sessions_lock:
            await add_chat_message(session_id, {"role": "user", "content": message})

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

        # Check for cancellation before making API call
        if asyncio.current_task().cancelled():
            await set_request_state(request_id, "cancelled", {
                "session_id": session_id,
                "conversation_id": session_conversation_id
            })
            return session_conversation_id

        # Update state to processing
        await set_request_state(request_id, "processing", {
            "session_id": session_id,
            "conversation_id": session_conversation_id,
            "api_call_start": time.time()
        })

        try:
            # Use async API call with timeout
            response = await asyncio.wait_for(
                current_anthropic_client.beta.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=1000,
                    messages=await get_chat_messages(session_id),
                    system=CLAUDE_SYSTEM_PROMPT,
                    tools=tools,
                    tool_choice={"type": "auto"},
                    betas=["token-efficient-tools-2025-02-19"]
                ),
                timeout=60.0  # 60 second timeout
            )
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
        except Exception as api_error:
            logger.error(f"Claude API error for request {request_id}: {str(api_error)}")
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

        # Handle tool calls
        while response.stop_reason == 'tool_use':
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
                raise StopIteration("ToolBlockMissingError")

            logger.debug(f"Executing tool: {tool_block.name} for user_id: {user_id} with input: {tool_block.input}")
            tool_execution_result = execute_tool(tool_block.name, tool_block.input, user_id)
            
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

            # If not the specific Asana auth error, proceed as before with Claude:
            async with chat_sessions_lock:
                # Convert tool_block to a serializable dict
                tool_block_dict = {
                    "type": "tool_use",
                    "id": tool_block.id,
                    "name": tool_block.name,
                    "input": tool_block.input
                }
                await add_chat_message(session_id, {"role": "assistant", "content": [tool_block_dict]})
                await add_chat_message(session_id, {"role": "user", "content": [{
                    "type": "tool_result",
                    "tool_use_id": tool_block.id,
                    "content": json.dumps(tool_execution_result) 
                }]})
            
            # Use async API call for tool response
            try:
                response = await asyncio.wait_for(
                    current_anthropic_client.beta.messages.create(
                        model="claude-sonnet-4-20250514",
                        max_tokens=1000,
                        messages=await get_chat_messages(session_id),
                        system=CLAUDE_SYSTEM_PROMPT,
                        tools=tools,
                        tool_choice={"type": "auto"},
                        betas=["token-efficient-tools-2025-02-19"]
                    ),
                    timeout=60.0  # 60 second timeout for tool responses too
                )
            except asyncio.TimeoutError:
                logger.error(f"Claude API timeout during tool use for request {request_id}")
                await set_request_state(request_id, "timeout", {
                    "session_id": session_id,
                    "conversation_id": session_conversation_id,
                    "error": "Claude API timed out during tool use"
                })
                raise
        
        # Add assistant final response to session history (if not an intercepted error)
        async with chat_sessions_lock:
            # Convert response.content to serializable format
            content_list = []
            for block in response.content:
                if block.type == 'text':
                    content_list.append({"type": "text", "text": block.text})
                elif block.type == 'tool_use':
                    content_list.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input
                    })
            await add_chat_message(session_id, {"role": "assistant", "content": content_list})
        
        # Extract and save assistant response to database
        assistant_response_text = ""
        if response.content and response.content[0].type == 'text':
            assistant_response_text = response.content[0].text
            
            # Save assistant message to database
            logger.info(f"About to save ASSISTANT message: conversation_id={session_conversation_id}, request_id={request_id}, content_preview='{assistant_response_text[:50]}...'")
            saved_conversation_id = save_message_to_db(user_id, session_conversation_id, assistant_response_text, "ASSISTANT", request_id)
            if saved_conversation_id:
                logger.info(f"ASSISTANT message saved successfully to conversation_id={saved_conversation_id} (original session_conversation_id={session_conversation_id})")
            else:
                logger.error(f"Failed to save ASSISTANT message for conversation_id={session_conversation_id}, request_id={request_id}")
            
            # Send structured response with request_id and conversation_id
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
            
            # Opportunistically clean up expired cache entries after successful response
        elif response.content: 
            logger.info("Claude's response did not end with a text block but was not a tool use. Sending a status or nothing.")
        else: 
            error_payload = {
                "error": "EmptyResponse", 
                "detail": "Assistant provided no content.",
                "request_id": request_id,
                "client_conversation_id": client_conversation_id
            }
            await safe_websocket_send(error_payload)

        return session_conversation_id
    
    except asyncio.CancelledError:
        # Handle task cancellation
        logger.info(f"Request {request_id} was cancelled")
        await set_request_state(request_id, "cancelled", {
            "session_id": session_id,
            "conversation_id": session_conversation_id,
            "cancelled_at": time.time()
        })
        raise
    
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