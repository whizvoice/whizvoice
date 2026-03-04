from fastapi import FastAPI, WebSocket, HTTPException, WebSocketDisconnect, Depends, Header, Request, File, UploadFile, Form
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

from anthropic import AsyncAnthropic, AuthenticationError, BadRequestError
from asana_tools import asana_tools, get_asana_tasks, get_asana_workspaces, get_current_date, get_current_datetime, get_parent_tasks, get_new_asana_task_id, update_asana_task, delete_asana_task, clear_workspace_preference_cache, get_workspace_preference, get_parent_task_preference, set_parent_task_preference, init_redis_client, _CREATE_TASK_DESC_PARENT_REQUIRED
from about_me_tool import about_me_tools, get_app_info, get_user_data
from screen_agent_tools import screen_agent_tools, agent_launch_app, agent_disable_continuous_listening, agent_set_tts_enabled, agent_close_app, agent_close_other_app, cancel_pending_screen_tools, agent_fitbit_add_quick_calories
from device_control_tools import device_control_tools, agent_set_alarm, agent_set_timer, agent_dismiss_alarm, agent_dismiss_timer, agent_stop_ringing, agent_dismiss_amdroid_alarm, agent_get_next_alarm, agent_delete_alarm, agent_toggle_flashlight, agent_draft_calendar_event, agent_save_calendar_event, agent_dial_phone_number, agent_press_call_button, agent_set_volume, agent_lookup_phone_contacts
from screen_agent_queue import screen_agent_queue
from autofix_trigger import schedule_autofix_trigger
from messaging_tools import messaging_tools, agent_whatsapp_select_chat, agent_whatsapp_send_message, agent_whatsapp_draft_message, agent_sms_select_chat, agent_sms_draft_message, agent_sms_send_message, agent_dismiss_draft
from music_tools import music_tools, agent_play_youtube_music, agent_queue_youtube_music, agent_pause_youtube_music, get_music_app_preference, set_music_app_preference
from maps_tools import maps_tools, agent_search_google_maps_location, agent_search_google_maps_phrase, agent_get_google_maps_directions, agent_recenter_google_maps, agent_fullscreen_google_maps, agent_select_location_from_list
from color_tools import color_tools, pick_random_color
from location_tools import location_tools, save_location
from contacts_tools import contacts_tools, add_contact_preference, get_contact_preference, list_contact_preferences, remove_contact_preference
from weather_tools import weather_tools, get_weather, set_temperature_units
from tool_result_handler import tool_result_handler
from preferences import set_preference, get_preference, ensure_user_and_prefs, get_decrypted_preference_key, set_encrypted_preference_key, CLAUDE_API_KEY_PREF_NAME, set_user_timezone
from auth import verify_google_token, create_access_token, get_current_user, AuthError, SECRET_KEY as AUTH_SECRET_KEY, ALGORITHM as AUTH_ALGORITHM, create_refresh_token
from supabase_client import supabase
from redis_managers import create_managers, MissingTimestampError
import stripe

# Import extracted modules
from models import (
    ChatMessage, ChatResponse, GoogleTokenRequest, TokenResponse,
    TestAuthRequest, RefreshTokenRequest, NewAccessTokenResponse,
    UserApiKeySetRequest, TokenUpdateRequest, ApiTokenStatusResponse,
    SetTimezoneRequest, ConversationCreate, ConversationUpdate,
    ConversationResponse, MessageCreate, MessageResponse,
    DialogflowWebhookRequest, DialogflowWebhookResponse,
    CreateCheckoutSessionRequest, CreateCheckoutSessionResponse,
    CancelSubscriptionResponse, SubscriptionStatusResponse,
    UiDumpCreate, UiDumpResponse,
    WakeWordAudioResponse
)
from database import (
    load_conversation_history,
    get_user_message_ids_since_last_bot,
    get_non_cancelled_bot_message_ids,
    save_message_to_db,
    update_tool_result_in_db
)
from cleanup_tasks import (
    cleanup_session,
    evict_user_sessions_if_needed,
    cleanup_abandoned_tool_executions,
    cleanup_stale_sessions
)
from billing import (
    set_stripe_config,
    create_stripe_checkout_session,
    get_user_subscription_status,
    cancel_user_subscription
)

try:
    from constants import STRIPE_SECRET_KEY, STRIPE_PRICE_ID
except ImportError:
    # For testing environments where constants.py might not exist
    STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
    STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID", "")
from redis_helpers import (
    # Chat session functions
    get_chat_messages, get_chat_messages_for_claude, add_chat_message, set_chat_messages, clear_chat_session, rename_chat_session, mark_chat_messages_cancelled, update_pending_result_timestamp, update_tool_results,
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
set_stripe_config(STRIPE_SECRET_KEY, STRIPE_PRICE_ID)

# System prompt for Claude
CLAUDE_SYSTEM_PROMPT = """You are Whiz Voice, a friendly AI chatbot that can help with anything. You have access to various tools that you MUST use when appropriate:

1. When the user asks to open/launch an app (like WhatsApp, YouTube, Maps, etc.), you MUST use the 'launch_app' tool
2. For WhatsApp messaging, use the WhatsApp-specific tools (whatsapp_select_chat, whatsapp_draft_message, whatsapp_send_message)
3. For SMS texting, use the SMS-specific tools (sms_select_chat, sms_draft_message, sms_send_message)
4. For Asana/task management, use the Asana tools
   - remember to use update_asana_task instead of get_new_asana_task_id if you are changing a task, to avoid creating duplicates.
   - Before creating a new task, check the parent task preference using get_parent_task_preference. If it returns 'true', you MUST always assign a parent task — ask the user if you're unsure which parent to use.
5. For app information, use the get_app_info tool
6. For music playback:
   - When the user asks to play music WITHOUT specifying an app, check their music app preference using get_music_app_preference
   - If no preference is set, ask the user which music app they prefer (currently we only support YouTube Music, not Spotify) and save it using set_music_app_preference
   - If the user explicitly specifies an app in their request (e.g., "play on YouTube Music"), use that app and optionally save it as their preference
7. For deciding on a random color when a list of colors isn't specified, ALWAYS use the pick_random_color tool
8. For weather, use the get_weather tool with the appropriate days_ahead parameter (0 = today, 1 = tomorrow, etc.)

IMPORTANT: You MUST ACTUALLY USE the appropriate tools for all actions rather than just describing what you would do.

You are a voice app. Please keep your responses BRIEF AND CONCISE so that they don't take too long to be read out loud. DO NOT comment or explain beyond the direct answer unless asked explicitly.

User messages sent to you are transcribed from audio. If it doesn't make sense, the transcription was probably inaccurate. Take your best guess of what the user meant to say.

The user may contradict themselves while thinking aloud. Remember: the most recent messages are the most true to their intent. 

FORMATTING: You can use markdown formatting in your responses (e.g., **bold**, *italic*, `code`, code blocks with triple backticks, lists, etc.) to improve readability. The app will render markdown appropriately.

DON'T DUPLICATE: You have access to the tool history and the success/failure of past tool calls. PLEASE CHECK THE HISTORY. Often multiple Asana tasks will be created as different versions of the same user intent, and YOU NEED TO PROACTIVELY DELETE THE OLD ONES.

PENDING RESULT: When you've requested something with a tool use and it hasn't completed yet, the tool result will say "Result pending..." or may indicate a specific wait reason (e.g., "Waiting for user to unlock phone..."). These will be updated later with the real tool result.

EXTRA NOTE ABOUT NAVIGATION: If you are about to close yourself but you used agent_get_google_maps_directions during the converation, please launch_app Google Maps before you close so the user can continue to navigate.
"""

# can concatenate additional tools here if needed
tools = asana_tools + about_me_tools + screen_agent_tools + device_control_tools + messaging_tools + music_tools + maps_tools + color_tools + location_tools + weather_tools + contacts_tools

# Pre-build variant with parent-required description for get_new_asana_task_id
tools_parent_required = []
for _tool in tools:
    if _tool.get('name') == 'get_new_asana_task_id':
        tools_parent_required.append({**_tool, 'description': _CREATE_TASK_DESC_PARENT_REQUIRED})
    else:
        tools_parent_required.append(_tool)

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
    # Initialize screen agent queue with execute_tool function
    screen_agent_queue.set_execute_tool_func(execute_tool)
    logger.info("Initialized screen agent queue")

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

async def load_validated_messages_from_db(conversation_id: int, session_id: str) -> Optional[List[Dict]]:
    """Load conversation from DB with tool_use/tool_result validation.

    Two-pass validation:
    1. Collect all tool_result IDs and valid (non-cancelled) tool_use IDs
    2. Build messages, skipping orphaned tool_use (no result) and orphaned tool_result (cancelled tool_use)

    Self-heals Redis by rewriting the session with validated messages.
    Returns the validated messages, or None on failure.
    """
    try:
        query = supabase.table("messages")\
            .select("id, content, message_sender, timestamp, request_id, cancelled, content_type, tool_content")\
            .eq("conversation_id", conversation_id)\
            .order("timestamp", desc=False)

        response = query.execute()
        db_messages = response.data if response.data else []

        # First pass: identify valid (non-cancelled) tool_use IDs and tool_result IDs
        tool_use_ids_with_results = set()
        valid_tool_use_ids = set()

        # Collect all tool_result IDs
        for msg in db_messages:
            if msg.get('content_type') == 'tool_result' and msg.get('tool_content'):
                for block in msg['tool_content']:
                    if isinstance(block, dict) and block.get('tool_use_id'):
                        tool_use_ids_with_results.add(block['tool_use_id'])

        # Collect valid (non-cancelled) tool_use IDs
        # Only count actual tool_use blocks, not server_tool_use (web search)
        for msg in db_messages:
            if not msg.get('cancelled') and msg.get('content_type') == 'tool_use' and msg.get('tool_content'):
                for block in msg['tool_content']:
                    if isinstance(block, dict) and block.get('type') == 'tool_use' and block.get('id'):
                        valid_tool_use_ids.add(block['id'])

        # Second pass: build messages, skipping incomplete/orphaned tool blocks
        redis_messages = []
        for msg in db_messages:
            # Skip cancelled messages
            if msg.get('cancelled'):
                continue

            content_type = msg.get('content_type', 'text')
            tool_content = msg.get('tool_content')

            # Handle tool_use messages - skip if no corresponding tool_result
            # Only check actual tool_use blocks, not server_tool_use (web search)
            if content_type == 'tool_use' and tool_content:
                tool_use_id = None
                for block in tool_content:
                    if isinstance(block, dict) and block.get('type') == 'tool_use' and block.get('id'):
                        tool_use_id = block['id']
                        break

                if tool_use_id and tool_use_id not in tool_use_ids_with_results:
                    logger.warning(f"[load_validated] Skipping incomplete tool_use (no result): {tool_use_id}")
                    continue

                redis_messages.append({"role": "assistant", "content": tool_content, "_timestamp": msg.get('timestamp')})
            elif content_type == 'tool_result' and tool_content:
                # Skip orphaned tool_results whose tool_use was cancelled
                tool_use_id = None
                for block in tool_content:
                    if isinstance(block, dict) and block.get('tool_use_id'):
                        tool_use_id = block['tool_use_id']
                        break

                if tool_use_id and tool_use_id not in valid_tool_use_ids:
                    logger.warning(f"[load_validated] Skipping orphaned tool_result (tool_use was cancelled): {tool_use_id}")
                    continue

                redis_messages.append({"role": "user", "content": tool_content, "_timestamp": msg.get('timestamp')})
            # Handle regular text messages
            elif msg['message_sender'] == 'USER':
                redis_messages.append({"role": "user", "content": msg['content'], "_timestamp": msg.get('timestamp')})
            elif msg['message_sender'] == 'ASSISTANT':
                if tool_content:
                    # Preserve server tool blocks (e.g. web search) for multi-turn context
                    redis_messages.append({"role": "assistant", "content": tool_content, "_timestamp": msg.get('timestamp')})
                else:
                    redis_messages.append({"role": "assistant", "content": msg['content'], "_timestamp": msg.get('timestamp')})

        # Self-heal Redis by rewriting the session with validated messages
        if redis_messages:
            await set_chat_messages(session_id, redis_messages)
            logger.info(f"[load_validated] Populated Redis session with {len(redis_messages)} validated messages from database")

        return redis_messages

    except Exception as e:
        logger.error(f"[load_validated] Error loading conversation history from database: {str(e)}")
        return None


def _has_orphaned_tool_uses(messages: List[Dict]) -> bool:
    """Quick scan for tool_use blocks without matching tool_result in the next user message.

    This checks the merged message list for the Claude API requirement that every
    tool_use in an assistant message must have a corresponding tool_result in the
    immediately following user message.
    """
    for i, msg in enumerate(messages):
        if msg.get('role') != 'assistant':
            continue
        content = msg.get('content', [])
        if not isinstance(content, list):
            continue
        # Collect tool_use IDs from this assistant message
        # Skip server_tool_use blocks (web search) - they don't need user-side tool_result
        tool_use_ids = set()
        for block in content:
            if isinstance(block, dict) and block.get('type') == 'tool_use' and block.get('id'):
                tool_use_ids.add(block['id'])
        if not tool_use_ids:
            continue
        # Check the next message for matching tool_results
        if i + 1 >= len(messages):
            return True
        next_msg = messages[i + 1]
        if next_msg.get('role') != 'user':
            return True
        next_content = next_msg.get('content', [])
        if not isinstance(next_content, list):
            return True
        result_ids = set()
        for block in next_content:
            if isinstance(block, dict) and block.get('type') == 'tool_result' and block.get('tool_use_id'):
                result_ids.add(block['tool_use_id'])
        if not tool_use_ids.issubset(result_ids):
            return True
    return False


def _fix_orphaned_tool_uses(messages: List[Dict]) -> None:
    """Fallback: insert synthetic tool_result blocks for orphaned tool_use blocks.

    Mutates messages in place. For each assistant message containing tool_use blocks
    without a corresponding tool_result in the next user message, inserts synthetic
    tool_result blocks.
    """
    i = 0
    while i < len(messages):
        msg = messages[i]
        if msg.get('role') != 'assistant':
            i += 1
            continue
        content = msg.get('content', [])
        if not isinstance(content, list):
            i += 1
            continue
        # Collect tool_use IDs from this assistant message
        # Skip server_tool_use blocks (web search) - they don't need user-side tool_result
        tool_use_ids = set()
        for block in content:
            if isinstance(block, dict) and block.get('type') == 'tool_use' and block.get('id'):
                tool_use_ids.add(block['id'])
        if not tool_use_ids:
            i += 1
            continue
        # Check what tool_results exist in the next message
        existing_result_ids = set()
        if i + 1 < len(messages) and messages[i + 1].get('role') == 'user':
            next_content = messages[i + 1].get('content', [])
            if isinstance(next_content, list):
                for block in next_content:
                    if isinstance(block, dict) and block.get('type') == 'tool_result' and block.get('tool_use_id'):
                        existing_result_ids.add(block['tool_use_id'])
        missing_ids = tool_use_ids - existing_result_ids
        if missing_ids:
            logger.warning(f"[_fix_orphaned_tool_uses] Inserting synthetic tool_results for: {missing_ids}")
            synthetic_blocks = [
                {"type": "tool_result", "tool_use_id": tid, "content": "Result pending - request was interrupted."}
                for tid in missing_ids
            ]
            if i + 1 < len(messages) and messages[i + 1].get('role') == 'user':
                # Prepend synthetic tool_results to existing user message
                next_content = messages[i + 1].get('content', [])
                if isinstance(next_content, list):
                    messages[i + 1]['content'] = synthetic_blocks + next_content
                else:
                    messages[i + 1]['content'] = synthetic_blocks + [{"type": "text", "text": next_content}]
            else:
                # Insert a new user message with synthetic tool_results
                messages.insert(i + 1, {"role": "user", "content": synthetic_blocks})
        i += 1


async def call_claude_api(client: AsyncAnthropic, session_id: str, stream: bool = None, conversation_id: Optional[int] = None, with_tools: bool = True, user_id: str = None):
    """
    Standard method to call Claude API with consistent parameters.

    Always uses stream=False with tools enabled for reliability.
    This ensures tools are always available when needed.

    Returns:
    - Coroutine for complete response (non-streaming)

    conversation_id: Optional - if provided, will reload context from DB if empty
    with_tools: Whether to include tools in the request (default True)
    """
    # Get messages from session (without internal metadata fields like _timestamp)
    # This returns new dicts, so we don't mutate the original messages in Redis
    messages = await get_chat_messages_for_claude(session_id)

    # SAFETY NET: If context is empty but we have a conversation_id, try to load from database
    # This handles edge cases where Redis session might have been cleared unexpectedly
    if len(messages) == 0 and conversation_id:
        logger.warning(f"[CLAUDE_CONTEXT] Empty context for conversation {conversation_id}, attempting to reload from database")
        try:
            from supabase_client import supabase
            query = supabase.table("messages")\
                .select("id, content, message_sender, timestamp, cancelled, content_type, tool_content, request_id")\
                .eq("conversation_id", conversation_id)\
                .order("timestamp", desc=False)

            response = query.execute()
            db_messages = response.data if response.data else []

            redis_messages = []
            # Build messages from database
            # NOTE: With atomic writes, tool_use and tool_result are always saved together,
            # so we no longer need to skip incomplete tool_use blocks
            for msg in db_messages:
                if msg.get('cancelled'):
                    continue

                content_type = msg.get('content_type', 'text')
                tool_content = msg.get('tool_content')

                # Handle tool_use messages
                if content_type == 'tool_use' and tool_content:
                    logger.info(f"📥 DB load: tool_use, db_timestamp={msg.get('timestamp')}")
                    redis_messages.append({"role": "assistant", "content": tool_content, "_timestamp": msg.get('timestamp'), "_request_id": msg.get('request_id')})
                elif content_type == 'tool_result' and tool_content:
                    logger.info(f"📥 DB load: tool_result, db_timestamp={msg.get('timestamp')}")
                    redis_messages.append({"role": "user", "content": tool_content, "_timestamp": msg.get('timestamp'), "_request_id": msg.get('request_id')})
                # Handle regular text messages
                elif msg['message_sender'] == 'USER':
                    logger.info(f"📥 DB load: USER text, db_timestamp={msg.get('timestamp')}")
                    redis_messages.append({"role": "user", "content": msg['content'], "_timestamp": msg.get('timestamp'), "_request_id": msg.get('request_id')})
                elif msg['message_sender'] == 'ASSISTANT':
                    logger.info(f"📥 DB load: ASSISTANT text, db_timestamp={msg.get('timestamp')}")
                    redis_messages.append({"role": "assistant", "content": msg['content'], "_timestamp": msg.get('timestamp'), "_request_id": msg.get('request_id')})

            if redis_messages:
                await set_chat_messages(session_id, redis_messages)
                # Re-fetch with stripping to ensure _timestamp is removed before sending to Claude
                messages = await get_chat_messages_for_claude(session_id)
                logger.info(f"[CLAUDE_CONTEXT] Reloaded {len(redis_messages)} messages from database")
        except Exception as e:
            logger.error(f"[CLAUDE_CONTEXT] Failed to reload context from database: {e}")

    # Always use non-streaming mode
    stream = False

    def _deduplicate_tool_results(blocks):
        """Keep only the last tool_result per tool_use_id."""
        seen = {}
        for i, b in enumerate(blocks):
            if isinstance(b, dict) and b.get('type') == 'tool_result':
                seen[b['tool_use_id']] = i
        # Keep blocks where: not a tool_result, OR it's the last one for its tool_use_id
        return [b for i, b in enumerate(blocks)
                if not (isinstance(b, dict) and b.get('type') == 'tool_result')
                or seen.get(b.get('tool_use_id')) == i]

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
                merged_content = _deduplicate_tool_results(tool_results) + text_blocks
            else:
                # For assistant messages: text blocks first, then other blocks (server_tool_use,
                # web_search_tool_result, tool_use) in their original order
                text_blocks = [b for b in prev_blocks + curr_blocks if isinstance(b, dict) and b.get('type') == 'text']
                other_blocks = [b for b in prev_blocks + curr_blocks if isinstance(b, dict) and b.get('type') != 'text']
                merged_content = text_blocks + other_blocks

            prev_msg['content'] = merged_content

    messages = merged_messages

    # Validate: check for orphaned tool_use blocks (no matching tool_result)
    # This can happen when user messages arrive while tools are executing
    if _has_orphaned_tool_uses(messages) and conversation_id:
        logger.warning(f"[CLAUDE_CONTEXT] Orphaned tool_use detected in session {session_id}, reloading from DB to self-heal")
        reloaded = await load_validated_messages_from_db(conversation_id, session_id)
        if reloaded:
            # Re-fetch stripped messages and re-run merge logic
            messages = await get_chat_messages_for_claude(session_id)
            merged_messages = []
            for msg in messages:
                if not merged_messages or merged_messages[-1]['role'] != msg['role']:
                    merged_messages.append(msg)
                else:
                    prev_msg = merged_messages[-1]
                    prev_content = prev_msg['content']
                    curr_content = msg['content']
                    prev_blocks = prev_content if isinstance(prev_content, list) else [{"type": "text", "text": prev_content}]
                    curr_blocks = curr_content if isinstance(curr_content, list) else [{"type": "text", "text": curr_content}]
                    if msg['role'] == 'user':
                        tool_results = [b for b in prev_blocks + curr_blocks if isinstance(b, dict) and b.get('type') == 'tool_result']
                        text_blocks = [b for b in prev_blocks + curr_blocks if isinstance(b, dict) and b.get('type') == 'text']
                        merged_content = _deduplicate_tool_results(tool_results) + text_blocks
                    else:
                        text_blocks = [b for b in prev_blocks + curr_blocks if isinstance(b, dict) and b.get('type') == 'text']
                        other_blocks = [b for b in prev_blocks + curr_blocks if isinstance(b, dict) and b.get('type') != 'text']
                        merged_content = text_blocks + other_blocks
                    prev_msg['content'] = merged_content
            messages = merged_messages
            logger.info(f"[CLAUDE_CONTEXT] Self-healed: reloaded and re-merged {len(messages)} messages")
        else:
            # Fallback: insert synthetic tool_results so the API call doesn't fail
            logger.warning(f"[CLAUDE_CONTEXT] DB reload failed, inserting synthetic tool_results as fallback")
            _fix_orphaned_tool_uses(messages)
    elif _has_orphaned_tool_uses(messages):
        # No conversation_id available, use fallback
        logger.warning(f"[CLAUDE_CONTEXT] Orphaned tool_use detected but no conversation_id, inserting synthetic tool_results")
        _fix_orphaned_tool_uses(messages)

    # Log the conversation context being sent to Claude
    logger.info(f"[CLAUDE_CONTEXT] Sending {len(messages)} messages to Claude for session {session_id}, stream={stream}")
    for i, msg in enumerate(messages):
        role = msg.get('role', 'unknown')
        content = msg.get('content', '')
        # Truncate content for logging
        content_preview = content[:100] + "..." if len(content) > 100 else content
        logger.info(f"[CLAUDE_CONTEXT] Message {i}: role={role}, content={content_preview}")

    # Pick the right pre-built tools list based on parent task preference
    tools_to_send = None
    if with_tools:
        if user_id and await get_parent_task_preference(user_id) == "true":
            tools_to_send = tools_parent_required
        else:
            tools_to_send = tools

    api_params = {
        "model": "claude-sonnet-4-5-20250929",
        "max_tokens": 1000,
        "messages": messages,
        "system": CLAUDE_SYSTEM_PROMPT,
        "stream": stream
    }

    # Only add tools-related params if we have tools
    if tools_to_send:
        # Web search is a server-side tool with a different schema than custom tools
        web_search_tool = {"type": "web_search_20250305", "name": "web_search", "max_uses": 3}
        api_params["tools"] = tools_to_send + [web_search_tool]
        api_params["tool_choice"] = {"type": "auto"}

    return client.messages.create(**api_params)

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
    "asana_parent_task_preference",
    "contacts",
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

# Per-conversation locks for subset detection to prevent race conditions
# when multiple messages arrive concurrently for the same conversation
subset_detection_locks: Dict[int, asyncio.Lock] = {}
subset_detection_locks_lock = asyncio.Lock()


async def get_subset_detection_lock(conversation_id: int) -> asyncio.Lock:
    """Get or create a per-conversation lock for subset detection"""
    async with subset_detection_locks_lock:
        if conversation_id not in subset_detection_locks:
            subset_detection_locks[conversation_id] = asyncio.Lock()
        return subset_detection_locks[conversation_id]



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
        
        # Share Redis client with asana_tools for cross-worker caching
        init_redis_client(redis_client)

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


def set_workspace_preference_with_cache_clear(user_id, key, value):
    """Set workspace preference and invalidate the in-memory cache."""
    result = set_preference(user_id, key, value)
    clear_workspace_preference_cache(user_id)
    return result

async def set_parent_task_preference_with_cache_clear(user_id, require_parent):
    """Set parent task preference (Redis cache is updated directly on set)."""
    result = await set_parent_task_preference(user_id, require_parent)
    return result

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
    "get_current_datetime": {
        "function_name": "get_current_datetime",
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
    "get_new_asana_task_id": {
        "function_name": "get_new_asana_task_id",
        "requires_auth": True,
        "is_async": True,
        "args_mapping": lambda args, user_id: (
            user_id,
            args.get('name'),
            args.get('due_date'),
            args.get('notes'),
            args.get('parent_task_gid'),
            args.get('assignee_email'),
            args.get('is_parent_task', False)
        ),
        "validation": lambda args: {"error": "Task name is required."} if not args.get('name') else None
    },
    "set_workspace_preference": {
        "function_name": "set_workspace_preference_with_cache_clear",
        "requires_auth": True,
        "args_mapping": lambda args, user_id: (user_id, 'asana_workspace_preference', args.get('workspace_gid')),
        "validation": lambda args: ValueError("Workspace GID is required for set_workspace_preference") if not args.get('workspace_gid') else None
    },
    "get_workspace_preference": {
        "function_name": "get_workspace_preference",
        "requires_auth": True,
        "args_mapping": lambda args, user_id: (user_id,),
        "validation": None
    },
    "get_parent_task_preference": {
        "function_name": "get_parent_task_preference",
        "requires_auth": True,
        "is_async": True,
        "args_mapping": lambda args, user_id: (user_id,),
        "validation": None
    },
    "set_parent_task_preference": {
        "function_name": "set_parent_task_preference_with_cache_clear",
        "requires_auth": True,
        "is_async": True,
        "args_mapping": lambda args, user_id: (user_id, args.get('require_parent')),
        "validation": lambda args: {"error": "require_parent is required."} if args.get('require_parent') is None else None
    },
    "update_asana_task": {
        "function_name": "update_asana_task",
        "requires_auth": True,
        "args_mapping": lambda args, user_id: (
            user_id,
            args.get('task_gid'),
            args.get('name'),
            args.get('due_date'),
            args.get('notes'),
            args.get('completed'),
            args.get('parent_gid')
        ),
        "validation": lambda args: (
            {"error": "Task GID is required."} if not args.get('task_gid') else None
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
    "agent_launch_app": {
        "function_name": "agent_launch_app",
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
    "agent_whatsapp_select_chat": {
        "function_name": "agent_whatsapp_select_chat",
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
    "agent_whatsapp_send_message": {
        "function_name": "agent_whatsapp_send_message",
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
    "agent_whatsapp_draft_message": {
        "function_name": "agent_whatsapp_draft_message",
        "requires_auth": False,
        "is_async": True,  # Mark this as an async tool
        "needs_websocket": True,  # This tool needs WebSocket context
        "args_mapping": lambda args, user_id, **kwargs: (
            args.get('message'),
            args.get('chat_name'),  # Required: recipient name/phone number
            user_id,
            kwargs.get('websocket'),
            kwargs.get('tool_result_handler'),
            kwargs.get('conversation_id'),
            args.get('previous_text')
        ),
        "validation": lambda args: {"error": "Message is required."} if not args.get('message') else ({"error": "chat_name is required."} if not args.get('chat_name') else None)
    },
    "agent_sms_select_chat": {
        "function_name": "agent_sms_select_chat",
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
    "agent_sms_draft_message": {
        "function_name": "agent_sms_draft_message",
        "requires_auth": False,
        "is_async": True,
        "needs_websocket": True,
        "args_mapping": lambda args, user_id, **kwargs: (
            args.get('message'),
            args.get('contact_name'),  # Required: recipient name/phone number
            user_id,
            kwargs.get('websocket'),
            kwargs.get('tool_result_handler'),
            kwargs.get('conversation_id'),
            args.get('previous_text')
        ),
        "validation": lambda args: {"error": "Message is required."} if not args.get('message') else ({"error": "contact_name is required."} if not args.get('contact_name') else None)
    },
    "agent_sms_send_message": {
        "function_name": "agent_sms_send_message",
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
    "agent_dismiss_draft": {
        "function_name": "agent_dismiss_draft",
        "requires_auth": False,
        "is_async": True,
        "needs_websocket": True,
        "args_mapping": lambda args, user_id, **kwargs: (
            user_id,
            kwargs.get('websocket'),
            kwargs.get('tool_result_handler'),
            kwargs.get('conversation_id')
        ),
        "validation": lambda args: None
    },
    "agent_disable_continuous_listening": {
        "function_name": "agent_disable_continuous_listening",
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
    "agent_set_tts_enabled": {
        "function_name": "agent_set_tts_enabled",
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
    "agent_close_app": {
        "function_name": "agent_close_app",
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
    "agent_close_other_app": {
        "function_name": "agent_close_other_app",
        "requires_auth": False,
        "is_async": True,
        "needs_websocket": True,
        "args_mapping": lambda args, user_id, **kwargs: (
            args.get('app_name'),
            user_id,
            kwargs.get('websocket'),
            kwargs.get('tool_result_handler'),
            kwargs.get('conversation_id')
        ),
        "validation": lambda args: {"error": "app_name is required."} if not args.get('app_name') else None
    },
    # ========== Device Control Tools (direct intents/APIs) ==========
    "agent_set_alarm": {
        "function_name": "agent_set_alarm",
        "requires_auth": False,
        "is_async": True,
        "needs_websocket": True,
        "args_mapping": lambda args, user_id, **kwargs: (
            args.get('hour'),
            args.get('minute'),
            args.get('label'),
            user_id,
            kwargs.get('websocket'),
            kwargs.get('tool_result_handler'),
            kwargs.get('conversation_id')
        ),
        "validation": lambda args: {"error": "hour is required."} if args.get('hour') is None else ({"error": "minute is required."} if args.get('minute') is None else None)
    },
    "agent_set_timer": {
        "function_name": "agent_set_timer",
        "requires_auth": False,
        "is_async": True,
        "needs_websocket": True,
        "args_mapping": lambda args, user_id, **kwargs: (
            args.get('seconds'),
            args.get('label'),
            user_id,
            kwargs.get('websocket'),
            kwargs.get('tool_result_handler'),
            kwargs.get('conversation_id')
        ),
        "validation": lambda args: {"error": "seconds is required."} if args.get('seconds') is None else None
    },
    "agent_dismiss_alarm": {
        "function_name": "agent_dismiss_alarm",
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
    "agent_dismiss_timer": {
        "function_name": "agent_dismiss_timer",
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
    "agent_dismiss_amdroid_alarm": {
        "function_name": "agent_dismiss_amdroid_alarm",
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
    "agent_stop_ringing": {
        "function_name": "agent_stop_ringing",
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
    "agent_get_next_alarm": {
        "function_name": "agent_get_next_alarm",
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
    "agent_delete_alarm": {
        "function_name": "agent_delete_alarm",
        "requires_auth": False,
        "is_async": True,
        "needs_websocket": True,
        "args_mapping": lambda args, user_id, **kwargs: (
            args.get('hour'),
            args.get('minute'),
            args.get('label'),
            user_id,
            kwargs.get('websocket'),
            kwargs.get('tool_result_handler'),
            kwargs.get('conversation_id')
        ),
        "validation": lambda args: {"error": "hour is required."} if args.get('hour') is None else ({"error": "minute is required."} if args.get('minute') is None else None)
    },
    "agent_toggle_flashlight": {
        "function_name": "agent_toggle_flashlight",
        "requires_auth": False,
        "is_async": True,
        "needs_websocket": True,
        "args_mapping": lambda args, user_id, **kwargs: (
            args.get('turn_on'),
            user_id,
            kwargs.get('websocket'),
            kwargs.get('tool_result_handler'),
            kwargs.get('conversation_id')
        ),
        "validation": lambda args: {"error": "turn_on is required."} if args.get('turn_on') is None else None
    },
    "agent_draft_calendar_event": {
        "function_name": "agent_draft_calendar_event",
        "requires_auth": False,
        "is_async": True,
        "needs_websocket": True,
        "args_mapping": lambda args, user_id, **kwargs: (
            args.get('title'),
            args.get('begin_time'),
            args.get('end_time'),
            args.get('description'),
            args.get('location'),
            args.get('all_day', False),
            args.get('attendees'),
            args.get('recurrence'),
            args.get('availability'),
            args.get('access_level'),
            args.get('timezone'),
            user_id,
            kwargs.get('websocket'),
            kwargs.get('tool_result_handler'),
            kwargs.get('conversation_id')
        ),
        "validation": lambda args: {"error": "title is required."} if not args.get('title') else ({"error": "begin_time is required."} if not args.get('begin_time') else None)
    },
    "agent_save_calendar_event": {
        "function_name": "agent_save_calendar_event",
        "requires_auth": False,
        "is_async": True,
        "needs_websocket": True,
        "args_mapping": lambda args, user_id, **kwargs: (
            args.get('title'),
            args.get('begin_time'),
            args.get('end_time'),
            args.get('description'),
            args.get('location'),
            args.get('all_day', False),
            args.get('recurrence'),
            args.get('availability'),
            args.get('access_level'),
            args.get('timezone'),
            user_id,
            kwargs.get('websocket'),
            kwargs.get('tool_result_handler'),
            kwargs.get('conversation_id')
        ),
        "validation": lambda args: {"error": "title is required."} if not args.get('title') else ({"error": "begin_time is required."} if not args.get('begin_time') else None)
    },
    "agent_dial_phone_number": {
        "function_name": "agent_dial_phone_number",
        "requires_auth": False,
        "is_async": True,
        "needs_websocket": True,
        "args_mapping": lambda args, user_id, **kwargs: (
            args.get('phone_number'),
            user_id,
            kwargs.get('websocket'),
            kwargs.get('tool_result_handler'),
            kwargs.get('conversation_id')
        ),
        "validation": lambda args: {"error": "phone_number is required."} if not args.get('phone_number') else None
    },
    "agent_press_call_button": {
        "function_name": "agent_press_call_button",
        "requires_auth": False,
        "is_async": True,
        "needs_websocket": True,
        "args_mapping": lambda args, user_id, **kwargs: (
            args.get('expected_number'),
            user_id,
            kwargs.get('websocket'),
            kwargs.get('tool_result_handler'),
            kwargs.get('conversation_id')
        ),
        "validation": lambda args: None
    },
    "agent_set_volume": {
        "function_name": "agent_set_volume",
        "requires_auth": False,
        "is_async": True,
        "needs_websocket": True,
        "args_mapping": lambda args, user_id, **kwargs: (
            args.get('volume_level'),
            args.get('stream', 'music'),
            user_id,
            kwargs.get('websocket'),
            kwargs.get('tool_result_handler'),
            kwargs.get('conversation_id')
        ),
        "validation": lambda args: {"error": "volume_level is required."} if args.get('volume_level') is None else None
    },
    "agent_lookup_phone_contacts": {
        "function_name": "agent_lookup_phone_contacts",
        "requires_auth": False,
        "is_async": True,
        "needs_websocket": True,
        "args_mapping": lambda args, user_id, **kwargs: (
            args.get('name'),
            user_id,
            kwargs.get('websocket'),
            kwargs.get('tool_result_handler'),
            kwargs.get('conversation_id')
        ),
        "validation": lambda args: {"error": "name is required."} if not args.get('name') else None
    },
    "agent_play_youtube_music": {
        "function_name": "agent_play_youtube_music",
        "requires_auth": False,
        "is_async": True,
        "needs_websocket": True,
        "args_mapping": lambda args, user_id, **kwargs: (
            args.get('query'),
            args.get('content_type', 'song'),
            user_id,
            kwargs.get('websocket'),
            kwargs.get('tool_result_handler'),
            kwargs.get('conversation_id')
        ),
        "validation": lambda args: {"error": "Query is required."} if not args.get('query') else None
    },
    "agent_queue_youtube_music": {
        "function_name": "agent_queue_youtube_music",
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
    "agent_pause_youtube_music": {
        "function_name": "agent_pause_youtube_music",
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
    "agent_search_google_maps_location": {
        "function_name": "agent_search_google_maps_location",
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
    "agent_search_google_maps_phrase": {
        "function_name": "agent_search_google_maps_phrase",
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
    "agent_get_google_maps_directions": {
        "function_name": "agent_get_google_maps_directions",
        "requires_auth": False,
        "is_async": True,
        "needs_websocket": True,
        "args_mapping": lambda args, user_id, **kwargs: (
            args.get('mode'),
            args.get('search'),
            args.get('position'),
            args.get('fragment'),
            user_id,
            kwargs.get('websocket'),
            kwargs.get('tool_result_handler'),
            kwargs.get('conversation_id')
        ),
        "validation": None
    },
    "agent_recenter_google_maps": {
        "function_name": "agent_recenter_google_maps",
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
    "agent_fullscreen_google_maps": {
        "function_name": "agent_fullscreen_google_maps",
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
    "agent_select_location_from_list": {
        "function_name": "agent_select_location_from_list",
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
            args.get('location'),  # None if not provided, which will default to 'weather_default'
            args.get('temperature_units')  # None if not provided, will use saved preference or default to 'us'
        ),
        "validation": lambda args: (
            {"error": "days_ahead must be a number."} if args.get('days_ahead') is not None and not isinstance(args.get('days_ahead'), int) else
            {"error": "temperature_units must be 'us' or 'si'."} if args.get('temperature_units') is not None and args.get('temperature_units') not in ['us', 'si'] else
            None
        )
    },
    "set_temperature_units": {
        "function_name": "set_temperature_units",
        "requires_auth": True,
        "is_async": True,
        "args_mapping": lambda args, user_id: (
            args.get('unit'),
            user_id
        ),
        "validation": lambda args: (
            {"error": "unit is required."} if not args.get('unit') else
            {"error": "unit must be 'us' or 'si'."} if args.get('unit') not in ['us', 'si'] else
            None
        )
    },
    "cancel_pending_screen_tools": {
        "function_name": "cancel_pending_screen_tools",
        "requires_auth": False,
        "is_async": True,
        "needs_websocket": True,
        "uses_kwargs": True,  # This tool takes **kwargs
        "args_mapping": lambda args, user_id, **kwargs: {
            "session_id": kwargs.get("session_id"),
            "user_id": user_id,
            "websocket": kwargs.get("websocket"),
            "conversation_id": kwargs.get("conversation_id")
        },
        "validation": None
    },
    "agent_fitbit_add_quick_calories": {
        "function_name": "agent_fitbit_add_quick_calories",
        "requires_auth": False,
        "is_async": True,
        "needs_websocket": True,
        "args_mapping": lambda args, user_id, **kwargs: (
            args.get('calories'),
            user_id,
            kwargs.get('websocket'),
            kwargs.get('tool_result_handler'),
            kwargs.get('conversation_id')
        ),
        "validation": lambda args: {"error": "Calories is required."} if not args.get('calories') else None
    },
    "add_contact_preference": {
        "function_name": "add_contact_preference",
        "requires_auth": True,
        "args_mapping": lambda args, user_id: (
            user_id,
            args.get('nickname'),  # Optional - defaults to real_name if not provided
            args.get('real_name'),
            args.get('preferred_app'),
            args.get('phone_number'),
            args.get('phone_label'),
            args.get('email'),
            args.get('email_label'),
            args.get('address'),
            args.get('address_label')
        ),
        "validation": lambda args: (
            {"error": "real_name is required."} if not args.get('real_name') else
            {"error": "preferred_app must be 'whatsapp' or 'sms'."} if args.get('preferred_app') not in ['whatsapp', 'sms'] else
            None
        )
    },
    "get_contact_preference": {
        "function_name": "get_contact_preference",
        "requires_auth": True,
        "is_async": True,
        "needs_websocket": True,
        "args_mapping": lambda args, user_id, **kwargs: (
            user_id,
            args.get('name'),
            kwargs.get('websocket'),
            kwargs.get('tool_result_handler'),
            kwargs.get('conversation_id')
        ),
        "validation": lambda args: {"error": "name is required."} if not args.get('name') else None
    },
    "list_contact_preferences": {
        "function_name": "list_contact_preferences",
        "requires_auth": True,
        "args_mapping": lambda args, user_id: (user_id,),
        "validation": None
    },
    "remove_contact_preference": {
        "function_name": "remove_contact_preference",
        "requires_auth": True,
        "args_mapping": lambda args, user_id: (user_id, args.get('name')),
        "validation": lambda args: {"error": "name is required."} if not args.get('name') else None
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
                # Check if tool uses kwargs instead of positional args
                if tool_config.get("uses_kwargs", False):
                    return await func(**func_args)
                else:
                    return await func(*func_args)
            else:
                logger.error(f"Tool {tool_name} marked as async but function is not async")
                raise ValueError(f"Tool {tool_name} misconfigured: marked as async but function is not async")
        else:
            # For sync tools, call normally
            if tool_config.get("uses_kwargs", False):
                return func(**func_args)
            else:
                return func(*func_args)
        
    except Exception as e:
        logger.error(f"Error executing tool {tool_name}: {str(e)}")
        raise e


async def execute_tool_with_queue(tool_name, tool_args, user_id: Optional[str] = None, **context):
    """
    Execute a tool, routing screen agent tools through the queue.

    Screen agent tools (those that operate on the device screen) are queued
    to ensure only one executes at a time per device. Other tools are
    executed directly.

    Args:
        tool_name: Name of the tool to execute
        tool_args: Arguments for the tool
        user_id: User ID if authenticated
        **context: Additional context (websocket, tool_result_handler, conversation_id, session_id, device_id, etc.)
    """
    # Check if this is a screen agent tool that needs queuing
    if screen_agent_queue.is_screen_agent_tool(tool_name):
        device_id = context.get("device_id")
        if not device_id:
            logger.warning(f"Screen agent tool {tool_name} called without device_id, returning error")
            return {
                "error": "device_id is required for screen agent tools. Please update your client.",
                "success": False
            }

        # Route through queue using device_id
        return await screen_agent_queue.enqueue(
            device_id=device_id,
            tool_name=tool_name,
            tool_args=tool_args,
            context={"user_id": user_id, **context}
        )
    else:
        # Non-screen agent tools execute directly
        return await execute_tool(tool_name, tool_args, user_id, **context)


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
    user_id = current_user.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID not found")

    return await create_stripe_checkout_session(
        user_id=user_id,
        email=current_user.get("email"),
        success_url=request_data.success_url,
        cancel_url=request_data.cancel_url
    )

@app.get("/subscription/status", response_model=SubscriptionStatusResponse)
async def get_subscription_status(current_user: dict = Depends(get_current_user)):
    """Get current subscription status for the user"""
    user_id = current_user.get("sub")
    email = current_user.get("email")

    if not user_id or not email:
        raise HTTPException(status_code=401, detail="User information incomplete")

    return await get_user_subscription_status(user_id=user_id, email=email)

@app.post("/subscription/cancel", response_model=CancelSubscriptionResponse)
async def cancel_subscription(current_user: dict = Depends(get_current_user)):
    """Cancel the user's subscription at period end"""
    user_id = current_user.get("sub")
    email = current_user.get("email")

    if not user_id or not email:
        raise HTTPException(status_code=401, detail="User information incomplete")

    return await cancel_user_subscription(user_id=user_id, email=email)

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

        # Get device_id from query parameters if provided (for screen agent queue)
        device_id = None
        if "device_id" in websocket.query_params:
            device_id = websocket.query_params["device_id"]
            logger.info(f"WebSocket connection with device_id={device_id}")
        
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
                optimistic_id_for_migration = None  # Track if we need to migrate an old optimistic session

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

                elif conversation_id is not None and conversation_id > 0:
                    # This is a real ID - check if there's an optimistic ID that maps to it
                    # This handles the case where a client reconnects with the real ID after disconnect
                    logger.info(f"Client connected with real conversation ID {conversation_id}, checking for optimistic session to migrate")
                    opt_result = supabase.table("conversations")\
                        .select("optimistic_chat_id")\
                        .eq("id", conversation_id)\
                        .eq("user_id", user_id)\
                        .is_("deleted_at", "null")\
                        .execute()

                    if opt_result.data and len(opt_result.data) > 0:
                        opt_chat_id = opt_result.data[0].get("optimistic_chat_id")
                        if opt_chat_id:
                            optimistic_id_for_migration = int(opt_chat_id)
                            logger.info(f"Found optimistic ID {optimistic_id_for_migration} for real conversation {conversation_id}")
                
                # Load conversation history and initialize session
                # Create a unique session ID per conversation, not just per user
                # Note: We keep the original conversation_id in the session_id for consistency,
                # but use actual_conversation_id for all operations
                if conversation_id is not None:
                    session_id = f"ws_{user_id}_conv_{conversation_id}"
                else:
                    # If no specific conversation, create a session for a new conversation
                    session_id = f"ws_{user_id}_new_{int(time.time())}"

                # MIGRATE OLD OPTIMISTIC SESSION: If we found an optimistic ID that maps to this real conversation,
                # migrate the old session to prevent forked contexts (fixes duplicate Asana task bug)
                if optimistic_id_for_migration is not None:
                    old_session_id = f"ws_{user_id}_conv_{optimistic_id_for_migration}"
                    if old_session_id != session_id:
                        logger.info(f"Migrating optimistic session {old_session_id} → {session_id}")
                        await rename_chat_session(old_session_id, session_id)

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
                        
                        logger.info(f"Received structured message with request_id: {request_id}, type: {message_type}, conversation_id: {message_conversation_id}, client_conversation_id: {client_conversation_id}, client_timestamp: {client_timestamp}")

                        # Require client_timestamp - fail fast if missing
                        if client_timestamp is None:
                            error_msg = f"Required timestamp missing in WebSocket message. request_id={request_id}, type={message_type}"
                            logger.error(f"MISSING_TIMESTAMP: {error_msg}")
                            await websocket.send_json({
                                "type": "error",
                                "code": "MISSING_TIMESTAMP",
                                "message": error_msg,
                                "request_id": request_id
                            })
                            raise MissingTimestampError(error_msg)

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

                    # Handle tool status messages (e.g., waiting for unlock)
                    if message_type == "tool_status":
                        tool_request_id = message_data.get("request_id")
                        status = message_data.get("status", "")
                        status_message = message_data.get("message", "")
                        logger.info(f"Received tool_status: request_id={tool_request_id}, status={status}")
                        if tool_request_id and status in ("waiting_for_unlock", "waiting_for_contacts_permission", "waiting_for_calendar_permission"):
                            metadata = tool_result_handler.extend_deadline(tool_request_id, 65.0)
                            # Update the Redis placeholder so Claude sees why the tool is waiting
                            if metadata and metadata.get('tool_use_id') and metadata.get('session_id'):
                                placeholder = ("Waiting for user to unlock phone..." if status == "waiting_for_unlock"
                                               else "Waiting for user to grant calendar permission..." if status == "waiting_for_calendar_permission"
                                               else "Waiting for user to grant contacts permission...")
                                await update_tool_results(
                                    metadata['session_id'],
                                    {metadata['tool_use_id']: json.dumps(placeholder)}
                                )
                                logger.info(f"Updated Redis placeholder for {metadata['tool_use_id']} to {status} status")
                        continue

                    # Handle cancellation requests
                    if message_type == "cancel":
                        cancel_request_id = message_data.get("cancel_request_id")
                        if cancel_request_id and redis_managers and "local_objects" in redis_managers:
                            task = await redis_managers["local_objects"].get_and_cancel_task(cancel_request_id)
                            if task:
                                logger.info(f"Cancelling request {cancel_request_id}")

                        # Cancel pending screen agent tools for this device
                        if device_id:
                            cancel_result = await screen_agent_queue.cancel_pending(device_id)
                            if cancel_result.get("cancelled_count", 0) > 0:
                                logger.info(f"Cancelled {cancel_result['cancelled_count']} pending screen agent tools for device {device_id}")

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
                        
                        # Calculate correct session_id per-message based on client_conversation_id
                        # This ensures messages are routed to the correct conversation's Redis context
                        # even if they arrive on a WebSocket that was originally opened for a different conversation
                        if client_conversation_id is not None and client_conversation_id < 0:
                            message_session_id = f"ws_{user_id}_conv_{client_conversation_id}"
                            message_conversation_id = client_conversation_id
                            logger.info(f"Routing message to per-message session_id={message_session_id} based on client_conversation_id={client_conversation_id}")
                        else:
                            message_session_id = session_id
                            message_conversation_id = session_conversation_id

                        # Create task for processing this message
                        task = asyncio.create_task(
                            process_message_task(
                                websocket=websocket,
                                session_id=message_session_id,
                                session_conversation_id=message_conversation_id,
                                user_id=user_id,
                                message=message,
                                request_id=request_id,
                                client_conversation_id=client_conversation_id,
                                client_message_id=client_message_id,
                                client_timestamp=client_timestamp,
                                device_id=device_id
                            )
                        )
                        
                        # Track the task using the message's session_id
                        if request_id and redis_managers:
                            if "local_objects" in redis_managers:
                                await redis_managers["local_objects"].add_task(request_id, task)
                            if "active_requests" in redis_managers:
                                await redis_managers["active_requests"].add(message_session_id, request_id)
                        
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
    
    # Local broadcasting removed - Redis pub/sub handles all delivery via redis_message_listener
    # This prevents duplicate messages (sessions were receiving via both Redis and local broadcast)


async def cancel_and_broadcast_messages(
    message_ids: List[int],
    conversation_id: int,
    request_id: str,
    reason: str,
    session_id: str = None
):
    """
    Cancel messages in database and broadcast DeleteMessage notifications to all clients.

    This centralizes the cancellation logic to ensure clients always receive notifications
    when messages are cancelled, preventing display of stale cancelled messages.

    Args:
        message_ids: List of message IDs to cancel
        conversation_id: Conversation ID for broadcasting
        request_id: Request ID for client matching
        reason: Reason for cancellation (e.g., "superseded_by_new_message", "superseded_by_new_request")
        session_id: Optional session ID for marking messages as cancelled in Redis
    """
    if not message_ids:
        return

    logger.info(f"Cancelling {len(message_ids)} message(s) and broadcasting to conversation {conversation_id}: {message_ids}")

    # Mark messages as cancelled in Redis (for proper filtering during concurrent requests)
    if session_id and request_id:
        try:
            await mark_chat_messages_cancelled(session_id, request_id)
            logger.info(f"Marked messages for request {request_id} as cancelled in Redis")
        except Exception as e:
            logger.error(f"Failed to mark messages as cancelled in Redis: {e}")

    # Mark messages as cancelled in database (single batch update)
    try:
        supabase.table("messages")\
            .update({"cancelled": "now()"})\
            .in_("id", message_ids)\
            .execute()
        logger.info(f"Marked {len(message_ids)} messages as cancelled in database")
    except Exception as e:
        logger.error(f"Failed to batch-cancel messages {message_ids}: {e}")

    # Broadcast DeleteMessage to all clients in conversation
    for msg_id in message_ids:
        delete_notification = {
            "type": "delete_message",
            "message_id": msg_id,
            "conversation_id": conversation_id,
            "request_id": request_id,
            "reason": reason
        }
        try:
            await broadcast_to_conversation(conversation_id, delete_notification)
            logger.info(f"Broadcasted delete notification for message {msg_id} to conversation {conversation_id}")
        except Exception as e:
            logger.error(f"Failed to broadcast delete notification for message {msg_id}: {e}")


async def detect_and_cancel_subset_requests(conversation_id: int, new_message_ids: List[int], websocket=None, session_id=None, new_request_id=None) -> List[str]:
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
            if old_message_ids and old_message_ids.issubset(new_message_ids_set) and request_id != new_request_id:
                logger.info(f"Request {request_id} (messages {old_message_ids}) is subset of new request (messages {new_message_ids_set})")

                # Get all ASSISTANT messages for this request and filter out tool messages
                # This prevents race condition where tool messages are being saved while we check
                try:
                    # Get ALL ASSISTANT messages with their content_type in a single query
                    bot_msg_result = supabase.table("messages")\
                        .select("id")\
                        .eq("request_id", request_id)\
                        .eq("message_sender", "ASSISTANT")\
                        .is_("cancelled", "null")\
                        .not_.in_("content_type", ["tool_use", "tool_result"])\
                        .execute()

                    if bot_msg_result.data:
                        message_ids_to_cancel = [bot_msg["id"] for bot_msg in bot_msg_result.data]

                        if message_ids_to_cancel:
                            # Store tuples for tracking
                            for msg_id in message_ids_to_cancel:
                                cancelled_bot_messages.append((msg_id, request_id))
                            logger.info(f"Found {len(message_ids_to_cancel)} bot messages to cancel for request {request_id}")
                        else:
                            logger.info(f"Request {request_id} only has tool_use/tool_result messages, skipping cancellation to preserve tool execution history")
                            continue  # Skip cancellation for this request
                except Exception as e:
                    logger.error(f"Error getting messages for request {request_id}: {e}")
                
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

        # Cancel and broadcast delete notifications for cancelled bot messages
        if cancelled_bot_messages:
            # Group messages by request_id for efficient cancellation
            for bot_message_id, request_id in cancelled_bot_messages:
                await cancel_and_broadcast_messages(
                    message_ids=[bot_message_id],
                    conversation_id=conversation_id,
                    request_id=request_id,
                    reason="superseded_by_new_request",
                    session_id=session_id
                )

        # For cancelled requests that had no bot messages yet (still processing),
        # send a delete_message notification so the client can remove the request
        # from pendingRequests and stop the typing indicator.
        cancelled_with_bot_messages = {req_id for _, req_id in cancelled_bot_messages}
        for req_id in cancelled_requests:
            if req_id not in cancelled_with_bot_messages:
                logger.info(f"Sending delete notification for request {req_id} with no bot messages (superseded before response)")
                delete_notification = {
                    "type": "delete_message",
                    "message_id": None,
                    "conversation_id": conversation_id,
                    "request_id": req_id,
                    "reason": "superseded_by_new_request"
                }
                try:
                    await broadcast_to_conversation(conversation_id, delete_notification)
                except Exception as e:
                    logger.error(f"Failed to broadcast delete notification for request {req_id}: {e}")
            
    except Exception as e:
        logger.error(f"Error detecting subset requests: {e}")
    
    return cancelled_requests

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
                                    conversation_id=actual_conversation_id,
                                    user_id=user_id
                                )
                                
                                # Create task to avoid coroutine reuse
                                retry_task = asyncio.create_task(retry_coroutine)
                                
                                # Use timeout like in normal flow
                                response = await asyncio.wait_for(retry_task, timeout=60.0)
                                
                                # Extract text response (skip server_tool_use/web_search_tool_result blocks)
                                assistant_response = ""
                                for block in (response.content or []):
                                    if block.type == 'text':
                                        assistant_response = block.text
                                        break
                                
                                if assistant_response:
                                    # Save the new assistant response (error message already deleted)
                                    logger.info(f"Saving new assistant response for conversation {actual_conversation_id}")
                                    local_objects = redis_managers.get("local_objects") if redis_managers else None
                                    save_result = save_message_to_db(
                                        user_id=user_id,
                                        conversation_id=actual_conversation_id,
                                        content=assistant_response,
                                        message_sender="ASSISTANT",
                                        request_id=request_id,
                                        content_type="text",
                                        local_objects=local_objects
                                    )

                                    if save_result:
                                        saved_conversation_id, message_id, cancelled_ids = save_result
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
                                local_objects = redis_managers.get("local_objects") if redis_managers else None
                                save_message_to_db(
                                    user_id=user_id,
                                    conversation_id=actual_conversation_id,
                                    content=error_content,
                                    message_sender="ASSISTANT",
                                    request_id=request_id,
                                    content_type="text",
                                    local_objects=local_objects
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
                                local_objects = redis_managers.get("local_objects") if redis_managers else None
                                save_message_to_db(
                                    user_id=user_id,
                                    conversation_id=actual_conversation_id,
                                    content=new_error_content,
                                    message_sender="ASSISTANT",
                                    request_id=request_id,
                                    content_type="text",
                                    local_objects=local_objects
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

@app.post("/ui-dumps", response_model=UiDumpResponse)
async def create_ui_dump(
    ui_dump: UiDumpCreate,
    current_user: Dict = Depends(get_current_user)
):
    """
    Upload a UI hierarchy dump from the screen agent when navigation fails.
    Used for debugging and improving screen agent code.
    """
    user_id = current_user.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="User not authenticated")

    try:
        # Build the insert data
        insert_data = {
            "user_id": user_id,
            "dump_reason": ui_dump.dump_reason,
            "ui_hierarchy": ui_dump.ui_hierarchy or "",
        }

        # Add optional fields if provided
        if ui_dump.error_message:
            insert_data["error_message"] = ui_dump.error_message
        if ui_dump.package_name:
            insert_data["package_name"] = ui_dump.package_name
        if ui_dump.device_model:
            insert_data["device_model"] = ui_dump.device_model
        if ui_dump.device_manufacturer:
            insert_data["device_manufacturer"] = ui_dump.device_manufacturer
        if ui_dump.android_version:
            insert_data["android_version"] = ui_dump.android_version
        if ui_dump.screen_width:
            insert_data["screen_width"] = ui_dump.screen_width
        if ui_dump.screen_height:
            insert_data["screen_height"] = ui_dump.screen_height
        if ui_dump.app_version:
            insert_data["app_version"] = ui_dump.app_version
        if ui_dump.conversation_id:
            insert_data["conversation_id"] = ui_dump.conversation_id
        if ui_dump.recent_actions:
            insert_data["recent_actions"] = ui_dump.recent_actions
        if ui_dump.screen_agent_context:
            insert_data["screen_agent_context"] = ui_dump.screen_agent_context

        # Insert into Supabase
        result = supabase.table("screen_agent_ui_dumps").insert(insert_data).execute()

        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to save UI dump")

        row = result.data[0]
        logger.info(f"Saved UI dump for user {user_id}: reason={ui_dump.dump_reason}, id={row['id']}")

        # Trigger auto-fix pipeline for screen agent errors (not rage shakes)
        if ui_dump.dump_reason != "rage_shake":
            asyncio.create_task(schedule_autofix_trigger())

        return UiDumpResponse(
            id=row["id"],
            created_at=row["created_at"]
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error saving UI dump for user {user_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to save UI dump")


@app.post("/wake-word-audio", response_model=WakeWordAudioResponse)
async def upload_wake_word_audio(
    file: UploadFile = File(...),
    phrase: str = Form(...),
    confidence: str = Form(...),
    accepted: str = Form(...),
    timestamp: str = Form(...),
    raw_vosk_json: str = Form(...),
    current_user: Dict = Depends(get_current_user)
):
    """
    Upload a wake word detection audio clip with metadata.
    Audio is stored in Supabase Storage, metadata in database.
    """
    user_id = current_user.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="User not authenticated")

    try:
        file_content = await file.read()
        file_size = len(file_content)

        storage_path = f"wake_word_audio/{user_id}/{file.filename}"

        supabase.storage.from_("wake-word-audio").upload(
            path=storage_path,
            file=file_content,
            file_options={"content-type": "audio/wav"}
        )

        confidence_val = float(confidence)
        accepted_val = accepted.lower() == "true"
        timestamp_val = int(timestamp)

        insert_data = {
            "user_id": user_id,
            "phrase": phrase,
            "confidence": confidence_val,
            "accepted": accepted_val,
            "detection_timestamp": timestamp_val,
            "raw_vosk_json": raw_vosk_json,
            "storage_path": storage_path,
            "file_size_bytes": file_size,
        }

        result = supabase.table("wake_word_audio_clips").insert(insert_data).execute()

        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to save audio clip metadata")

        row = result.data[0]
        logger.info(f"Saved wake word audio for user {user_id}: phrase={phrase}, confidence={confidence_val}, accepted={accepted_val}, id={row['id']}")

        return WakeWordAudioResponse(
            id=row["id"],
            created_at=row["created_at"]
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error uploading wake word audio: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to upload audio: {str(e)}")


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
async def catch_all(path: str, request: Request):
    print(f"Unmatched request: {request.method} /{path}")
    return JSONResponse({"error": "Not found"}, status_code=404)

async def process_message_task(websocket, session_id, session_conversation_id, user_id, message, request_id, client_conversation_id=None, client_message_id=None, client_timestamp=None, device_id=None):
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
            validated = await load_validated_messages_from_db(session_conversation_id, session_id)
            if validated is not None:
                messages = validated
        
        logger.info(f"Processing message in session {session_id}, conversation {session_conversation_id}, context length: {len(messages)}")
        
        # Save user message to database and update session_conversation_id
        logger.info(f"About to save user message. Current session_conversation_id: {session_conversation_id}")
        local_objects = redis_managers.get("local_objects") if redis_managers else None
        save_result = save_message_to_db(user_id, session_conversation_id, message, "USER", request_id, client_conversation_id, client_timestamp, content_type="text", local_objects=local_objects)
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

        real_conversation_id, user_message_id, cancelled_ids = save_result
        logger.info(f"After saving user message. Updated session_conversation_id: {real_conversation_id}, message_id: {user_message_id}")
        # Note: USER messages shouldn't have cancelled_ids (only ASSISTANT messages cancel previous ones)

        # Debug logging for optimistic ID migration investigation
        logger.info(f"🔧 MIGRATION_DEBUG: session_conversation_id={session_conversation_id}, real_conversation_id={real_conversation_id}")
        logger.info(f"🔧 MIGRATION_DEBUG: client_conversation_id={client_conversation_id}")
        logger.info(f"🔧 MIGRATION_DEBUG: Is migration? {real_conversation_id and real_conversation_id != session_conversation_id}")
        logger.info(f"🔧 MIGRATION_DEBUG: websocket is {'present' if websocket else 'None'}")
        if websocket:
            try:
                logger.info(f"🔧 MIGRATION_DEBUG: websocket.client_state={websocket.client_state}")
            except Exception as state_err:
                logger.warning(f"🔧 MIGRATION_DEBUG: Could not get websocket.client_state: {state_err}")

        # Update optimistic → real mapping if client provided optimistic ID
        if client_conversation_id and client_conversation_id < 0 and real_conversation_id:
            if redis_managers and "session_mappings" in redis_managers:
                try:
                    await redis_managers["session_mappings"].set_mapping(
                        session_id, client_conversation_id, real_conversation_id
                    )
                    logger.info(f"Mapped optimistic ID {client_conversation_id} → real ID {real_conversation_id}")

                    # Also cache in local_objects for broadcast lookups
                    if "local_objects" in redis_managers:
                        await redis_managers["local_objects"].cache_id_mapping(
                            client_conversation_id, real_conversation_id
                        )
                        logger.info(f"Cached mapping in local_objects: {client_conversation_id} → {real_conversation_id}")

                    # PROACTIVE SESSION MIGRATION: Rename the session to use the real ID
                    # This ensures that if the client reconnects with the real ID, they get the same session
                    old_session_id = f"ws_{user_id}_conv_{client_conversation_id}"
                    new_session_id = f"ws_{user_id}_conv_{real_conversation_id}"
                    if old_session_id != new_session_id:
                        await rename_chat_session(old_session_id, new_session_id)
                        logger.info(f"Proactively migrated session: {old_session_id} → {new_session_id}")
                        # Update local session_id variable to use new ID for rest of this request
                        session_id = new_session_id

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

        # NO LONGER MIGRATING WEBSOCKET: The WebSocket stays subscribed to its original ID
        # Broadcasting already handles sending to both optimistic and real conversation IDs
        # This prevents the WebSocket from being closed during optimistic->real ID migration
        if real_conversation_id and real_conversation_id != session_conversation_id:
            logger.info(f"Mapped optimistic ID {session_conversation_id} → real ID {real_conversation_id}")
            # DON'T call update_websocket_conversation() - leave the WebSocket on its original ID
            # The broadcast system will send messages to both the optimistic and real conversation IDs

        # Use real conversation ID for all database operations and message processing
        # The WebSocket remains subscribed to session_conversation_id (which may be optimistic)
        # but we use real_conversation_id for everything else
        processing_conversation_id = real_conversation_id if real_conversation_id else session_conversation_id

        # Wait for any pending tool executions to complete before processing new message
        # This prevents race conditions where new messages arrive while tools are still executing
        if processing_conversation_id:
            pending_count = await get_pending_tools_count(processing_conversation_id)
            if pending_count > 0:
                logger.info(f"Waiting for {pending_count} pending tool(s) to complete before processing new message")
                tools_completed = await wait_for_pending_tools(processing_conversation_id, timeout_seconds=5.0)
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
                if len(current_messages) == 0 and processing_conversation_id:
                    # Try to load any existing messages from the database
                    logger.info(f"Loading existing messages for conversation {processing_conversation_id} before adding new message")
                    try:
                        query = supabase.table("messages")\
                            .select("id, content, message_sender, timestamp, request_id, cancelled, content_type, tool_content")\
                            .eq("conversation_id", processing_conversation_id)\
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

                                redis_messages.append({"role": "assistant", "content": tool_content, "_timestamp": msg.get('timestamp')})
                            elif content_type == 'tool_result' and tool_content:
                                redis_messages.append({"role": "user", "content": tool_content, "_timestamp": msg.get('timestamp')})
                            # Handle regular text messages
                            elif msg['message_sender'] == 'USER':
                                redis_messages.append({"role": "user", "content": msg['content'], "_timestamp": msg.get('timestamp')})
                            elif msg['message_sender'] == 'ASSISTANT':
                                redis_messages.append({"role": "assistant", "content": msg['content'], "_timestamp": msg.get('timestamp')})

                        if redis_messages:
                            await set_chat_messages(session_id, redis_messages)
                            logger.info(f"Pre-loaded {len(redis_messages)} existing messages into Redis session")
                    except Exception as e:
                        logger.error(f"Failed to pre-load existing messages: {e}")
                        # Continue without context rather than fail

                # No separator needed - USER messages with tool_result will naturally merge with
                # subsequent user messages, and the tool_result will remain first in the merged content
                # Now add the current message (with client_timestamp to ensure correct ZSET ordering)
                await asyncio.shield(add_chat_message(session_id, {"role": "user", "content": message}, timestamp=client_timestamp))
                logger.info(f"Added current message to Redis session for conversation {processing_conversation_id}")

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
                if has_incomplete_after_add and processing_conversation_id:
                    logger.warning(f"Redis has incomplete tool_use after adding message, reloading from database for conversation {processing_conversation_id}")
                    try:
                        query = supabase.table("messages")\
                            .select("id, content, message_sender, timestamp, request_id, cancelled, content_type, tool_content")\
                            .eq("conversation_id", processing_conversation_id)\
                            .order("timestamp", desc=False)

                        response = query.execute()
                        db_messages = response.data if response.data else []

                        # Build complete history from DB, skipping incomplete/orphaned tool blocks
                        redis_messages = []
                        tool_use_ids_with_results = set()
                        valid_tool_use_ids = set()

                        # Collect all tool_result IDs
                        for msg in db_messages:
                            if msg.get('content_type') == 'tool_result' and msg.get('tool_content'):
                                for block in msg['tool_content']:
                                    if isinstance(block, dict) and block.get('tool_use_id'):
                                        tool_use_ids_with_results.add(block['tool_use_id'])

                        # Collect valid (non-cancelled) tool_use IDs
                        for msg in db_messages:
                            if not msg.get('cancelled') and msg.get('content_type') == 'tool_use' and msg.get('tool_content'):
                                for block in msg['tool_content']:
                                    if isinstance(block, dict) and block.get('id'):
                                        valid_tool_use_ids.add(block['id'])

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
                                redis_messages.append({"role": "assistant", "content": tool_content, "_timestamp": msg.get('timestamp')})
                            elif content_type == 'tool_result' and tool_content:
                                # Skip orphaned tool_results whose tool_use was cancelled
                                tool_use_id = None
                                for block in tool_content:
                                    if isinstance(block, dict) and block.get('tool_use_id'):
                                        tool_use_id = block['tool_use_id']
                                        break

                                if tool_use_id and tool_use_id not in valid_tool_use_ids:
                                    logger.warning(f"Skipping orphaned tool_result in reload (tool_use was cancelled): {tool_use_id}")
                                    continue

                                redis_messages.append({"role": "user", "content": tool_content, "_timestamp": msg.get('timestamp')})
                            elif msg['message_sender'] == 'USER':
                                redis_messages.append({"role": "user", "content": msg['content'], "_timestamp": msg.get('timestamp')})
                            elif msg['message_sender'] == 'ASSISTANT':
                                redis_messages.append({"role": "assistant", "content": msg['content'], "_timestamp": msg.get('timestamp')})

                        if redis_messages:
                            await set_chat_messages(session_id, redis_messages)
                            logger.info(f"Reloaded {len(redis_messages)} messages from DB to fix incomplete tool execution")
                    except Exception as e:
                        logger.error(f"Failed to reload from DB after incomplete tool detection: {e}")
        except Exception as e:
            logger.error(f"Failed to add message to Redis: {e}")
            # Continue anyway - better to process without full context than to fail

        # Track which message IDs this request is responding to
        # Use processing_conversation_id for database queries
        # CRITICAL: Lock per-conversation to prevent race condition when multiple messages
        # arrive concurrently (e.g. from retry queue). Without this lock, Task B's
        # detect_and_cancel_subset_requests() may run before Task A's set_messages()
        # completes, causing subset detection to miss requests not yet registered in Redis.
        subset_lock = await get_subset_detection_lock(processing_conversation_id)
        async with subset_lock:
            user_message_ids = get_user_message_ids_since_last_bot(processing_conversation_id)
            if redis_managers and "request_messages" in redis_managers:
                await redis_managers["request_messages"].set_messages(
                    request_id, user_message_ids, processing_conversation_id
                )
                logger.info(f"Tracking request {request_id} responding to message IDs: {user_message_ids}")

            # Detect and cancel subset requests
            cancelled_requests = await detect_and_cancel_subset_requests(
                processing_conversation_id, user_message_ids, websocket, session_id,
                new_request_id=request_id
            )
            if cancelled_requests:
                logger.info(f"Cancelled {len(cancelled_requests)} subset requests for conversation {processing_conversation_id}")

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
            # Use processing_conversation_id for database operations
            api_coroutine = await call_claude_api(current_anthropic_client, session_id, conversation_id=processing_conversation_id, user_id=user_id)
            
            # The coroutine needs to be awaited to get the actual response
            # Create a task so it can be cancelled if needed
            api_task = asyncio.create_task(api_coroutine)
            
            # Await the task with timeout to get the actual stream or response
            api_result = await asyncio.wait_for(api_task, timeout=60.0)
            
            # Debug logging to understand what we got
            logger.info(f"API result type after awaiting: {type(api_result).__name__}, module: {getattr(type(api_result), '__module__', 'unknown')}")
            
            # Now check if we got a streaming response or a regular message
            # AsyncStream is from anthropic module and is for streaming
            # Message is for non-streaming responses (including tool calls)
            is_streaming = (
                type(api_result).__name__ == 'AsyncStream' or
                'AsyncStream' in str(type(api_result)) or
                'AsyncMessageStream' in str(type(api_result))
            ) and type(api_result).__name__ not in ('Message', 'BetaMessage')
            
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
                        # Skip server_tool_use and web_search_tool_result chunks (web search)
                        # - only extract text for the streaming response
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
            local_objects = redis_managers.get("local_objects") if redis_managers else None
            save_result = save_message_to_db(
                user_id=user_id,
                conversation_id=session_conversation_id,
                content=error_json_content,
                message_sender="ASSISTANT",
                request_id=request_id,
                content_type="text",
                local_objects=local_objects
            )
            if save_result:
                saved_conversation_id, error_message_id, cancelled_ids = save_result
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

                # Don't send error to client - stream was cancelled by subset detection
                # The newer request will provide the response
                return session_conversation_id

            # Handle BadRequestError (400) from Claude API
            if isinstance(api_error, BadRequestError):
                logger.error(f"Claude API BadRequest (400) for request {request_id}: {str(api_error)}")
                await set_request_state(request_id, "bad_request_error", {
                    "session_id": session_id,
                    "conversation_id": session_conversation_id,
                    "error": str(api_error)
                })

                # Create user-friendly error message
                error_message = "I encountered an error processing your message. This might be due to a technical issue with the conversation history. Please try starting a new conversation."

                # Create error payload
                error_payload = {
                    "type": "error",
                    "code": "CLAUDE_API_ERROR",
                    "message": error_message,
                    "request_id": request_id,
                    "conversation_id": session_conversation_id,
                    "client_conversation_id": client_conversation_id,
                    "client_message_id": client_message_id
                }

                # Save error as ASSISTANT message to database (persists even if WebSocket fails)
                logger.info(f"Saving BadRequest error as ASSISTANT message for conversation {session_conversation_id}")
                error_json_content = json.dumps(error_payload)
                local_objects = redis_managers.get("local_objects") if redis_managers else None
                save_result = save_message_to_db(
                    user_id=user_id,
                    conversation_id=session_conversation_id,
                    content=error_json_content,
                    message_sender="ASSISTANT",
                    request_id=request_id,
                    content_type="text",
                    local_objects=local_objects
                )
                if save_result:
                    saved_conversation_id, error_message_id, cancelled_ids = save_result
                    logger.info(f"BadRequest error saved as message {error_message_id} in conversation {saved_conversation_id}")
                else:
                    logger.error(f"Failed to save BadRequest error message to database")

                # Log error to screen_agent_ui_dumps table for debugging
                try:
                    error_dump_data = {
                        "user_id": user_id,
                        "dump_reason": "BadRequestError from Claude API",
                        "error_message": str(api_error),
                        "conversation_id": session_conversation_id,
                        "ui_hierarchy": "",
                    }
                    supabase.table("screen_agent_ui_dumps").insert(error_dump_data).execute()
                    logger.info(f"Logged BadRequest error to screen_agent_ui_dumps for conversation {session_conversation_id}")
                except Exception as dump_error:
                    logger.error(f"Failed to log error to screen_agent_ui_dumps: {dump_error}")

                # Try to send via WebSocket (may fail, but error is persisted in DB)
                await safe_websocket_send(error_payload)

                # Don't raise - let the client handle the error gracefully
                return session_conversation_id

            # Handle other API errors
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

            # For other errors, save generic error message to database
            error_message = "An unexpected error occurred. Please try again."
            error_payload = {
                "type": "error",
                "code": "INTERNAL_ERROR",
                "message": error_message,
                "request_id": request_id,
                "conversation_id": session_conversation_id,
                "client_conversation_id": client_conversation_id,
                "client_message_id": client_message_id
            }

            # Save error to database
            logger.info(f"Saving generic error as ASSISTANT message for conversation {session_conversation_id}")
            error_json_content = json.dumps(error_payload)
            local_objects = redis_managers.get("local_objects") if redis_managers else None
            try:
                save_result = save_message_to_db(
                    user_id=user_id,
                    conversation_id=session_conversation_id,
                    content=error_json_content,
                    message_sender="ASSISTANT",
                    request_id=request_id,
                    content_type="text",
                    local_objects=local_objects
                )
                if save_result:
                    saved_conversation_id, error_message_id, cancelled_ids = save_result
                    logger.info(f"Generic error saved as message {error_message_id} in conversation {saved_conversation_id}")
            except Exception as save_error:
                logger.error(f"Failed to save generic error message to database: {save_error}")

            # Try to send via WebSocket
            await safe_websocket_send(error_payload)

            # Don't raise - error has been handled
            return session_conversation_id

        # Check for cancellation after API call
        if asyncio.current_task().cancelled():
            await set_request_state(request_id, "cancelled", {
                "session_id": session_id,
                "conversation_id": session_conversation_id
            })
            return session_conversation_id

        # Check for application-level cancellation (subset detection)
        if redis_managers and "request_messages" in redis_managers:
            request_data = await redis_managers["request_messages"].get_messages(request_id)
            if request_data and request_data.get("status") == "cancelled":
                logger.info(f"Request {request_id} was cancelled by subset detection, skipping response processing")
                return session_conversation_id

        # Log the initial response to see if Claude is trying to use tools
        logger.info(f"Claude response stop_reason: {response.stop_reason}")
        if hasattr(response, 'content'):
            logger.info(f"Claude response content: {[{'type': getattr(block, 'type', 'unknown'), 'text': (getattr(block, 'text', '') or '')[:100] if hasattr(block, 'text') else None} for block in response.content]}")

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

            # Check for application-level cancellation (subset detection)
            if redis_managers and "request_messages" in redis_managers:
                request_data = await redis_managers["request_messages"].get_messages(request_id)
                if request_data and request_data.get("status") == "cancelled":
                    logger.info(f"Request {request_id} was cancelled by subset detection during tool loop, skipping")
                    return session_conversation_id

            # Log all content blocks from AI response
            logger.info(f"AI response stop_reason: {response.stop_reason}")
            logger.info(f"AI response content blocks: {[{'type': block.type, 'name': getattr(block, 'name', None)} for block in response.content]}")
            
            # Extract ALL tool_uses from response (not just first one)
            # Claude can return multiple tool_uses in one response
            tool_blocks = [block for block in response.content if block.type == 'tool_use']
            if not tool_blocks:
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

            logger.info(f"AI attempting to execute {len(tool_blocks)} tool(s): {[tb.name for tb in tool_blocks]}")
            for tb in tool_blocks:
                logger.info(f"Tool '{tb.name}' input parameters: {json.dumps(tb.input, indent=2)}")
                logger.debug(f"Executing tool: {tb.name} for user_id: {user_id} with input: {tb.input}")

            # Increment pending tool counter before execution (once per tool)
            for _ in tool_blocks:
                await increment_pending_tools(session_conversation_id)

            # ===== CRITICAL: Save tool_use, TEXT, and PENDING tool_result IMMEDIATELY =====
            # This prevents race conditions where Claude sees incomplete conversation history
            # and triggers the same tool multiple times

            # Convert ALL tool_blocks to serializable dicts
            tool_block_dicts = []
            for tb in tool_blocks:
                tool_block_dicts.append({
                    "type": "tool_use",
                    "id": tb.id,
                    "name": tb.name,
                    "input": tb.input
                })

            # Serialize server tool blocks (server_tool_use + web_search_tool_result) for multi-turn context
            # These are server-side tool blocks that don't need user-side execution
            server_tool_block_dicts = []
            for block in response.content:
                if block.type == 'server_tool_use':
                    server_tool_block_dicts.append({
                        "type": "server_tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input if hasattr(block, 'input') else {}
                    })
                elif block.type == 'web_search_tool_result':
                    # Content is Pydantic objects - serialize to dicts for JSON storage
                    raw_content = block.content if hasattr(block, 'content') else []
                    if isinstance(raw_content, list):
                        serialized_content = [item.model_dump() if hasattr(item, 'model_dump') else item for item in raw_content]
                    elif hasattr(raw_content, 'model_dump'):
                        serialized_content = raw_content.model_dump()
                    else:
                        serialized_content = raw_content
                    server_tool_block_dicts.append({
                        "type": "web_search_tool_result",
                        "tool_use_id": block.tool_use_id,
                        "content": serialized_content
                    })

            # Extract ALL text blocks from response - all text goes BEFORE tool_uses
            # Claude API expects: ASSISTANT [text, tool_use_A, tool_use_B] then USER [tool_result_A, tool_result_B]
            text_before_tool = []
            assistant_response_text_before = ""

            for block in response.content:
                if block.type == 'text':
                    text_dict = {"type": "text", "text": block.text}
                    text_before_tool.append(text_dict)
                    if not assistant_response_text_before:
                        assistant_response_text_before = block.text

            # Calculate timestamps to ensure correct ordering in database
            # Text must come BEFORE tool_use, so we use explicit timestamps
            from datetime import datetime, timedelta

            # For subsequent tool loop iterations, use last_tool_result_timestamp as base
            # This ensures each iteration's messages have timestamps AFTER the previous iteration
            # First iteration: last_tool_result_timestamp is None, use client_timestamp
            # Subsequent iterations: use last_tool_result_timestamp so timestamps increment
            #
            # NOTE: We intentionally do NOT check latest_redis_timestamp from other requests
            # because that would cause this request's timestamps to jump forward incorrectly,
            # breaking the tool_use/tool_result ordering for this request.
            if last_tool_result_timestamp:
                user_timestamp = last_tool_result_timestamp
            else:
                user_timestamp = client_timestamp

            # If client_timestamp not available, try database as fallback
            if not user_timestamp:
                user_msg_result = supabase.table("messages")\
                    .select("timestamp")\
                    .eq("conversation_id", session_conversation_id)\
                    .eq("request_id", request_id)\
                    .eq("message_sender", "USER")\
                    .execute()
                if user_msg_result.data:
                    user_timestamp = user_msg_result.data[0]["timestamp"]

            if user_timestamp:
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
                # Set timestamps: text_before+tool_use (T+1ms), tool_result (T+2ms)
                # text_before and tool_use are merged into ONE message with timestamp T+1ms
                text_before_and_tool_use_timestamp = (user_dt + timedelta(milliseconds=1)).isoformat().replace('+00:00', 'Z')
                tool_result_timestamp = (user_dt + timedelta(milliseconds=2)).isoformat().replace('+00:00', 'Z')
                last_tool_result_timestamp = tool_result_timestamp  # Track for final message ordering
                logger.info(f"Using explicit timestamps from client_timestamp: text_before_and_tool_use={text_before_and_tool_use_timestamp}, tool_result={tool_result_timestamp}")
            else:
                # Fail fast if no timestamp available for tool execution
                error_msg = f"Required timestamp missing for tool execution. No client_timestamp and no USER message found with request_id {request_id}"
                logger.error(f"MISSING_TIMESTAMP: {error_msg}")
                await websocket.send_json({
                    "type": "error",
                    "code": "MISSING_TIMESTAMP",
                    "message": error_msg,
                    "request_id": request_id
                })
                raise MissingTimestampError(error_msg)

            # Save assistant message to Redis BEFORE executing the tool
            # IMPORTANT: Only include text BEFORE tool_use, not after
            # 🔒 USE DISTRIBUTED REDIS LOCK to prevent race conditions across worker processes
            # Without this, concurrent requests can read incomplete conversation history
            lock_key = f"conversation_lock:{session_conversation_id}"
            lock = None

            try:
                # Acquire distributed lock - blocks other workers from reading context during tool_use + pending result creation
                if redis_client:
                    lock = redis_client.lock(lock_key, timeout=10, blocking_timeout=5)
                    acquired = await lock.acquire(blocking=True, blocking_timeout=5)
                    if not acquired:
                        logger.error(f"Failed to acquire conversation lock for {session_conversation_id}, falling back to local lock")
                        lock = None  # Fall back to local lock if Redis lock fails

                # If Redis lock failed or unavailable, use local lock as fallback
                if lock is None:
                    logger.warning(f"Using local lock (not distributed) for conversation {session_conversation_id}")
                    await chat_sessions_lock.acquire()

                # Build assistant content: text_before + web search blocks + ALL tool_uses (NO text_after)
                # Server tool blocks (server_tool_use, web_search_tool_result) must be preserved
                # for multi-turn context so Claude knows what it already searched
                assistant_content = text_before_tool + server_tool_block_dicts + tool_block_dicts

                # CRITICAL: Create pending tool_result for EACH tool_use IMMEDIATELY
                # Add BOTH assistant message and pending results to Redis ATOMICALLY
                # This prevents race condition where another worker reads tool_use without its tool_result
                pending_tool_result_dicts = []
                for tb in tool_blocks:
                    pending_tool_result_dicts.append({
                        "type": "tool_result",
                        "tool_use_id": tb.id,
                        "content": "Result pending..."
                    })
                # ATOMIC: Add both messages to Redis in a single transaction
                # This ensures no other worker can read partial state (tool_use without tool_result)
                await add_chat_message(
                    session_id,
                    [
                        {"role": "assistant", "content": assistant_content},
                        {"role": "user", "content": pending_tool_result_dicts}
                    ],
                    timestamp=[text_before_and_tool_use_timestamp, tool_result_timestamp],
                    request_id=request_id
                )

                # Save merged text_before+web search blocks+ALL tool_uses to database as ONE message
                # content_type is "tool_use" even though it may also contain text/web search content
                # This ensures proper timestamp ordering: merged message at T+1ms
                local_objects = redis_managers.get("local_objects") if redis_managers else None
                tool_use_save_result = save_message_to_db(
                    user_id=user_id,
                    conversation_id=session_conversation_id,
                    content=assistant_response_text_before,  # Include text_before (may be empty string)
                    message_sender="ASSISTANT",
                    request_id=request_id,
                    content_type="tool_use",  # Type is tool_use even with text/web search content
                    tool_content=server_tool_block_dicts + tool_block_dicts,  # Server tool blocks + ALL tool_uses
                    client_timestamp=text_before_and_tool_use_timestamp,
                    local_objects=local_objects
                )
                tool_names = [tb.name for tb in tool_blocks]
                if assistant_response_text_before:
                    logger.info(f"✅ Saved merged text+tool_use message to database: text='{assistant_response_text_before[:50]}...', tools={tool_names}")
                else:
                    logger.info(f"✅ Saved tool_use message to database (no text_before): tools={tool_names}")

                # Get the actual timestamp of the tool_use message we just saved
                # This is critical for ensuring the tool_result has the correct timestamp (+1ms after tool_use)
                if tool_use_save_result:
                    tool_use_conv_id, tool_use_msg_id, cancelled_ids = tool_use_save_result
                    # Note: tool_use messages shouldn't cancel previous messages (only text messages do)
                    tool_use_msg_result = supabase.table("messages")\
                        .select("timestamp")\
                        .eq("id", tool_use_msg_id)\
                        .execute()

                    if tool_use_msg_result.data:
                        # Parse the tool_use timestamp and add 1ms for tool_result
                        tool_use_timestamp_str = tool_use_msg_result.data[0]["timestamp"].replace('Z', '+00:00')
                        if '.' in tool_use_timestamp_str:
                            parts = tool_use_timestamp_str.split('.')
                            if len(parts) == 2:
                                if '+' in parts[1]:
                                    frac, tz = parts[1].split('+')
                                    frac = frac.ljust(6, '0')[:6]
                                    tool_use_timestamp_str = f"{parts[0]}.{frac}+{tz}"
                                elif '-' in parts[1]:
                                    frac, tz = parts[1].split('-')
                                    frac = frac.ljust(6, '0')[:6]
                                    tool_use_timestamp_str = f"{parts[0]}.{frac}-{tz}"

                        tool_use_dt = datetime.fromisoformat(tool_use_timestamp_str)
                        # Calculate tool_result timestamp as tool_use timestamp + 1ms
                        tool_result_timestamp = (tool_use_dt + timedelta(milliseconds=1)).isoformat().replace('+00:00', 'Z')
                        logger.info(f"Calculated tool_result_timestamp={tool_result_timestamp} (tool_use + 1ms)")
                    else:
                        logger.warning(f"Could not retrieve tool_use timestamp for message_id={tool_use_msg_id}, using fallback")
                        # Fallback to original logic if we can't get the timestamp
                        tool_result_timestamp = (user_dt + timedelta(milliseconds=2)).isoformat().replace('+00:00', 'Z')
                else:
                    logger.warning("tool_use_save_result is None, using fallback timestamp")
                    tool_result_timestamp = (user_dt + timedelta(milliseconds=2)).isoformat().replace('+00:00', 'Z')

                # pending_tool_result_dicts was already created and added to Redis above (atomically with tool_use)
                # Now save it to the database with the calculated timestamp

                # Save ALL PENDING tool_results to database with timestamp after tool_use
                pending_result_save = save_message_to_db(
                    user_id=user_id,
                    conversation_id=session_conversation_id,
                    content="",  # Tool messages don't have text content
                    message_sender="USER",
                    request_id=request_id,
                    content_type="tool_result",
                    tool_content=pending_tool_result_dicts,  # ALL pending results
                    client_timestamp=tool_result_timestamp,
                    local_objects=local_objects
                )
                tool_use_ids = [tb.id for tb in tool_blocks]
                logger.info(f"✅ Saved {len(tool_blocks)} PENDING tool_result(s) to database BEFORE execution: tool_use_ids={tool_use_ids}")

                # Sync Redis with the recalculated timestamp (DB uses tool_use + 1ms)
                await update_pending_result_timestamp(session_id, tool_use_ids, tool_result_timestamp)

                # Lock will be released in finally block - other workers can now see the pending tool_result

            finally:
                # Release the distributed lock or local lock
                if lock is not None and redis_client:
                    # Redis distributed lock
                    try:
                        is_owned = await lock.owned()
                        if is_owned:
                            await lock.release()
                            logger.debug(f"✅ Released distributed conversation lock for {session_conversation_id}")
                    except Exception as e:
                        logger.error(f"Error releasing distributed lock: {e}")
                elif lock is None and not redis_client:
                    # Local lock fallback
                    if chat_sessions_lock.locked():
                        chat_sessions_lock.release()
                        logger.debug(f"Released local conversation lock")

            # ===== Now execute ALL tools in parallel =====
            try:
                # Debug logging
                logger.info(f"🔧 TOOL_DEBUG execute_tool: About to execute {len(tool_blocks)} tool(s): {[tb.name for tb in tool_blocks]}")
                logger.info(f"🔧 TOOL_DEBUG execute_tool: session_id={session_id}, conversation_id={session_conversation_id}")
                logger.info(f"🔧 TOOL_DEBUG execute_tool: websocket is {'present' if websocket else 'None'}")
                if websocket:
                    try:
                        logger.info(f"🔧 TOOL_DEBUG execute_tool: websocket.client_state={websocket.client_state}")
                    except Exception as state_err:
                        logger.warning(f"🔧 TOOL_DEBUG execute_tool: Could not get websocket.client_state: {state_err}")
                logger.info(f"🔧 TOOL_DEBUG execute_tool: client_conversation_id={client_conversation_id} (optimistic ID migration: {client_conversation_id is not None and client_conversation_id < 0})")

                # Execute ALL tools using execute_tool_with_queue
                # Screen agent tools will be queued (one at a time), others run in parallel
                async def execute_single_tool(tb):
                    """Execute a single tool and return (tool_block, result)"""
                    # Set contextvars so wait_for_tool_result can store metadata
                    # linking request_id -> tool_use_id/session_id for extend_deadline
                    from tool_result_handler import _current_tool_use_id, _current_session_id
                    _current_tool_use_id.set(tb.id)
                    _current_session_id.set(session_id)
                    result = await execute_tool_with_queue(
                        tb.name,
                        tb.input,
                        user_id,
                        websocket=websocket,
                        tool_result_handler=tool_result_handler,
                        conversation_id=session_conversation_id,
                        session_id=session_id,
                        device_id=device_id
                    )
                    return (tb, result)

                tool_tasks = [execute_single_tool(tb) for tb in tool_blocks]
                tool_results = await asyncio.gather(*tool_tasks, return_exceptions=True)

                # Process results and check for special errors
                tool_execution_results = {}  # Maps tool_use_id -> result
                asana_auth_error = None
                for item in tool_results:
                    if isinstance(item, Exception):
                        logger.error(f"Tool execution failed with exception: {item}")
                        continue
                    tb, result = item
                    tool_execution_results[tb.id] = result

                    # Check for Asana auth error
                    if isinstance(result, dict) and \
                       result.get("status_code") == 401 and \
                       "Asana authentication failed" in result.get("error", ""):
                        logger.info(f"Tool {tb.name} resulted in Asana auth error. Sending directly to client.")
                        result["request_id"] = request_id
                        result["client_conversation_id"] = client_conversation_id
                        await safe_websocket_send(result)
                        asana_auth_error = result

                if asana_auth_error:
                    raise StopIteration("AsanaAuthErrorHandled")

                # ===== Update ALL PENDING tool_results with actual results =====
                # Build a dict of tool_updates: tool_use_id -> result content (JSON string)
                tool_updates = {}
                for tb in tool_blocks:
                    if tb.id not in tool_execution_results:
                        logger.warning(f"No result for tool_use_id={tb.id}, skipping update")
                        continue

                    tool_updates[tb.id] = json.dumps(tool_execution_results[tb.id])

                    # Update database with actual result
                    update_success = update_tool_result_in_db(
                        conversation_id=session_conversation_id,
                        tool_use_id=tb.id,
                        result_content=tool_execution_results[tb.id],
                        user_id=user_id
                    )
                    if update_success:
                        logger.info(f"✅ Updated database with actual tool_result for tool: {tb.name}")
                    else:
                        logger.error(f"❌ Failed to update database with actual tool_result for tool: {tb.name}")

                # Efficiently update only the changed messages in Redis (not all messages)
                if tool_updates:
                    updated_count = await update_tool_results(session_id, tool_updates)
                    logger.info(f"✅ Updated {updated_count} messages in Redis with actual tool_results")

                # NOTE: We no longer broadcast after each tool result replacement.
                # The broadcast will happen at the END of the conversation turn,
                # after Claude returns with stop_reason != 'tool_use'.
                # This prevents premature broadcasts while Claude is still in a tool loop.
            finally:
                # Always decrement counter for ALL tools, even if tool execution or saving fails
                for _ in tool_blocks:
                    await decrement_pending_tools(session_conversation_id)
            
            # Use async API call for tool response
            try:
                # Tool responses should not stream (we explicitly pass stream=False)
                tool_coroutine = await call_claude_api(
                    current_anthropic_client,
                    session_id,
                    stream=False,  # Explicitly disable streaming for tool responses
                    conversation_id=session_conversation_id,
                    user_id=user_id
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
                local_objects = redis_managers.get("local_objects") if redis_managers else None
                save_result = save_message_to_db(
                    user_id=user_id,
                    conversation_id=session_conversation_id,
                    content=error_json_content,
                    message_sender="ASSISTANT",
                    request_id=request_id,
                    content_type="text",
                    local_objects=local_objects
                )
                if save_result:
                    saved_conversation_id, error_message_id, cancelled_ids = save_result
                    logger.info(f"Authentication error saved as message {error_message_id} in conversation {saved_conversation_id}")
                else:
                    logger.error(f"Failed to save authentication error message to database")

                # Try to send via WebSocket (may fail, but error is persisted in DB)
                await safe_websocket_send(error_payload)

                # Don't raise - let the client handle the error gracefully
                return session_conversation_id
            except Exception as tool_api_error:
                # Check if this is a BadRequestError (400) from Claude API during tool use
                if isinstance(tool_api_error, BadRequestError):
                    # Check if this is specifically a tool_use_id mismatch error
                    is_tool_use_id_error = (
                        "tool_use_id" in str(tool_api_error).lower() or
                        "unexpected tool_use_id" in str(tool_api_error).lower()
                    )

                    if is_tool_use_id_error:
                        logger.error(f"Claude API tool_use_id mismatch error during tool use for request {request_id}: {str(tool_api_error)}")
                        logger.error(f"Conversation history corrupted - cannot continue")
                        error_message = "I'm sorry, but this conversation has encountered a technical issue with the message history that prevents me from continuing. Please start a new conversation."
                        error_code = "CONVERSATION_HISTORY_ERROR"
                    else:
                        # General BadRequestError during tool use (e.g., duplicate content, invalid format)
                        logger.error(f"Claude API BadRequest (400) during tool use for request {request_id}: {str(tool_api_error)}")
                        error_message = "I encountered an error processing your message. This might be due to a technical issue with the conversation history. Please try starting a new conversation."
                        error_code = "CLAUDE_API_ERROR"

                    await set_request_state(request_id, "bad_request_error", {
                        "session_id": session_id,
                        "conversation_id": session_conversation_id,
                        "error": str(tool_api_error)
                    })

                    # Create error payload
                    error_payload = {
                        "type": "error",
                        "code": error_code,
                        "message": error_message,
                        "request_id": request_id,
                        "conversation_id": session_conversation_id,
                        "client_conversation_id": client_conversation_id,
                        "client_message_id": client_message_id
                    }

                    # Save error as ASSISTANT message to database
                    logger.info(f"Saving BadRequest error (during tool use) as ASSISTANT message for conversation {session_conversation_id}")
                    error_json_content = json.dumps(error_payload)
                    local_objects = redis_managers.get("local_objects") if redis_managers else None
                    save_result = save_message_to_db(
                        user_id=user_id,
                        conversation_id=session_conversation_id,
                        content=error_json_content,
                        message_sender="ASSISTANT",
                        request_id=request_id,
                        content_type="text",
                        local_objects=local_objects
                    )
                    if save_result:
                        saved_conversation_id, error_message_id, cancelled_ids = save_result
                        logger.info(f"BadRequest error (during tool use) saved as message {error_message_id} in conversation {saved_conversation_id}")
                    else:
                        logger.error(f"Failed to save BadRequest error message to database")

                    # Log error to screen_agent_ui_dumps table for debugging
                    try:
                        error_dump_data = {
                            "user_id": user_id,
                            "dump_reason": f"BadRequestError during tool use: {error_code}",
                            "error_message": str(tool_api_error),
                            "conversation_id": session_conversation_id,
                        }
                        supabase.table("screen_agent_ui_dumps").insert(error_dump_data).execute()
                        logger.info(f"Logged BadRequest error to screen_agent_ui_dumps for conversation {session_conversation_id}")
                    except Exception as dump_error:
                        logger.error(f"Failed to log error to screen_agent_ui_dumps: {dump_error}")

                    # Try to send via WebSocket
                    await safe_websocket_send(error_payload)

                    # Don't raise - let the client handle the error gracefully
                    return session_conversation_id
                else:
                    # Re-raise other exceptions (non-BadRequestError)
                    raise

        # Log final response details
        logger.info(f"Final AI response - stop_reason: {response.stop_reason}")
        logger.info(f"Final AI response - content blocks: {[{'type': block.type, 'text': (getattr(block, 'text', '') or '')[:100] if hasattr(block, 'text') else None} for block in response.content]}")

        # Handle pause_turn stop reason (web search can produce this for long-running searches)
        # For v1, treat it the same as end_turn - extract whatever text Claude already returned
        if hasattr(response, 'stop_reason') and response.stop_reason == 'pause_turn':
            logger.warning(f"Received pause_turn stop_reason for request {request_id} - treating as end_turn. Response may be truncated.")

        # Extract text content from response
        assistant_response_text = ""
        all_text_parts = []
        content_list = []
        # Also collect server tool blocks for DB persistence (multi-turn context)
        server_tool_blocks = []
        # Collect citation URLs from web search text blocks
        citation_urls = []
        if response.content:
            for block in response.content:
                if block.type == 'text':
                    content_list.append({"type": "text", "text": block.text})
                    all_text_parts.append(block.text)
                    # Collect citations from web search results
                    if hasattr(block, 'citations') and block.citations:
                        for citation in block.citations:
                            url = getattr(citation, 'url', None) if hasattr(citation, 'url') else (citation.get('url') if isinstance(citation, dict) else None)
                            title = getattr(citation, 'title', None) if hasattr(citation, 'title') else (citation.get('title') if isinstance(citation, dict) else None)
                            if url and url not in [c[0] for c in citation_urls]:
                                citation_urls.append((url, title))
                elif block.type == 'server_tool_use':
                    server_tool_blocks.append({
                        "type": "server_tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input if hasattr(block, 'input') else {}
                    })
                elif block.type == 'web_search_tool_result':
                    # Content is Pydantic objects - serialize to dicts for JSON storage
                    raw_content = block.content if hasattr(block, 'content') else []
                    if isinstance(raw_content, list):
                        serialized_content = [item.model_dump() if hasattr(item, 'model_dump') else item for item in raw_content]
                    elif hasattr(raw_content, 'model_dump'):
                        serialized_content = raw_content.model_dump()
                    else:
                        serialized_content = raw_content
                    server_tool_blocks.append({
                        "type": "web_search_tool_result",
                        "tool_use_id": block.tool_use_id,
                        "content": serialized_content
                    })
                # Skip tool_use blocks - they were already added in the tool loop

        # Combine all text parts and append citation sources if present
        combined_text = "\n\n".join(all_text_parts) if len(all_text_parts) > 1 else (all_text_parts[0] if all_text_parts else "")
        if citation_urls:
            sources_section = "\n\n**Sources:**\n" + "\n".join(
                f"- [{title or url}]({url})" for url, title in citation_urls
            )
            combined_text += sources_section
            logger.info(f"Appended {len(citation_urls)} citation sources to response")
        assistant_response_text = combined_text

        # Calculate timestamp for final assistant text (after tool_result if applicable)
        final_text_timestamp = None
        if last_tool_result_timestamp:  # last_tool_result_timestamp was set in the tool loop
            # Parse tool_result timestamp and add 1ms to ensure final text comes after
            try:
                from datetime import datetime, timedelta
                tool_result_dt = datetime.fromisoformat(last_tool_result_timestamp.replace('Z', '+00:00'))
                final_text_timestamp = (tool_result_dt + timedelta(milliseconds=1)).isoformat().replace('+00:00', 'Z')
                logger.info(f"Setting final assistant text timestamp to {final_text_timestamp} (after tool_result)")
            except Exception as e:
                error_msg = f"Failed to parse tool_result timestamp '{last_tool_result_timestamp}': {e}"
                logger.error(f"MISSING_TIMESTAMP: {error_msg}")
                await websocket.send_json({
                    "type": "error",
                    "code": "MISSING_TIMESTAMP",
                    "message": error_msg,
                    "request_id": request_id
                })
                raise MissingTimestampError(error_msg)
        else:
            # For non-tool responses, use client_timestamp + 1ms offset
            # This ensures assistant response is ordered immediately after user message
            # (matches tool flow pattern and CLAUDE.md timestamp constraints)
            from datetime import datetime, timedelta
            if client_timestamp:
                try:
                    timestamp_str = client_timestamp.replace('Z', '+00:00')
                    user_dt = datetime.fromisoformat(timestamp_str)
                    final_text_timestamp = (user_dt + timedelta(milliseconds=1)).isoformat().replace('+00:00', 'Z')
                except Exception as e:
                    error_msg = f"Failed to parse client_timestamp '{client_timestamp}': {e}"
                    logger.error(f"MISSING_TIMESTAMP: {error_msg}")
                    await websocket.send_json({
                        "type": "error",
                        "code": "MISSING_TIMESTAMP",
                        "message": error_msg,
                        "request_id": request_id
                    })
                    raise MissingTimestampError(error_msg)
            else:
                # No client_timestamp for non-tool response - this should never happen
                # since we validate client_timestamp at message intake
                error_msg = f"Required timestamp missing for non-tool response. request_id={request_id}"
                logger.error(f"MISSING_TIMESTAMP: {error_msg}")
                await websocket.send_json({
                    "type": "error",
                    "code": "MISSING_TIMESTAMP",
                    "message": error_msg,
                    "request_id": request_id
                })
                raise MissingTimestampError(error_msg)

        # Add assistant final response to session history (if not an intercepted error)
        # IMPORTANT: Only add text blocks here, NOT tool_use blocks
        # Tool_use blocks were already added in the tool loop above

        # STEP 1: Check if request was cancelled BEFORE adding to Redis
        # This prevents race condition where cancelled message appears in context for other requests
        request_cancelled = False
        if redis_managers and "request_messages" in redis_managers:
            request_data = await redis_managers["request_messages"].get_messages(request_id)
            if request_data and request_data.get("status") == "cancelled":
                request_cancelled = True
                logger.info(f"Request {request_id} was cancelled, skipping add to Redis session")

        # STEP 2: Only add to Redis if NOT cancelled
        # Include web search blocks in Redis content for multi-turn context
        redis_content_list = content_list + server_tool_blocks if server_tool_blocks else content_list
        async with chat_sessions_lock:
            # Only add message if there's text content AND not already cancelled
            if content_list and not request_cancelled:
                await add_chat_message(session_id, {"role": "assistant", "content": redis_content_list}, timestamp=final_text_timestamp, request_id=request_id)

                # STEP 3: Check AGAIN after adding to handle race condition
                # where request was cancelled during the add operation
                if redis_managers and "request_messages" in redis_managers:
                    request_data = await redis_managers["request_messages"].get_messages(request_id)
                    if request_data and request_data.get("status") == "cancelled":
                        request_cancelled = True
                        logger.info(f"Request {request_id} was cancelled during add, marking message as cancelled in Redis")
                        await mark_chat_messages_cancelled(session_id, request_id)

        # Check for duplicate responses (race condition where multiple requests have same message set)
        # Skip saving if another request already saved a response for this conversation after the user messages
        skip_duplicate_response = False
        if redis_managers and "request_messages" in redis_managers and assistant_response_text and not request_cancelled:
            try:
                current_request_data = await redis_managers["request_messages"].get_messages(request_id)
                if current_request_data:
                    current_message_ids = current_request_data.get("message_ids", [])
                    if current_message_ids:
                        # Get timestamp of the latest user message we're responding to
                        max_user_message_id = max(current_message_ids)
                        user_msg_result = supabase.table("messages")\
                            .select("timestamp")\
                            .eq("id", max_user_message_id)\
                            .limit(1)\
                            .execute()

                        if user_msg_result.data:
                            user_message_timestamp = user_msg_result.data[0]["timestamp"]

                            # Check if another request already saved a response after these user messages
                            existing_msg = supabase.table("messages")\
                                .select("id")\
                                .eq("conversation_id", processing_conversation_id)\
                                .eq("message_sender", "ASSISTANT")\
                                .eq("content_type", "text")\
                                .is_("cancelled", "null")\
                                .neq("request_id", request_id)\
                                .gte("timestamp", user_message_timestamp)\
                                .limit(1)\
                                .execute()

                            if existing_msg.data:
                                logger.info(f"Skipping duplicate response for request {request_id} - another request already saved a response after user message {max_user_message_id}")
                                skip_duplicate_response = True
            except Exception as e:
                logger.error(f"Error checking for duplicate responses: {e}")

        # Save assistant message to database if there's text content (always save, but mark as cancelled if needed)
        # If web search blocks are present, save them as tool_content for multi-turn context
        saved_conversation_id = session_conversation_id
        save_result = None
        if assistant_response_text and not skip_duplicate_response:
            logger.info(f"About to save ASSISTANT message: conversation_id={session_conversation_id}, request_id={request_id}, cancelled={request_cancelled}, content_preview='{assistant_response_text[:50]}...'")
            local_objects = redis_managers.get("local_objects") if redis_managers else None
            # Only save as tool_use if there are actual client-side tool_use blocks
            # server_tool_use blocks (e.g. web search) are server-side and should be saved as text
            # so the Android sync API includes them
            has_client_tool_use = any(block.type == 'tool_use' for block in response.content)
            save_content_type = "tool_use" if has_client_tool_use else "text"
            # Still save server tool blocks in tool_content for multi-turn context
            save_tool_content = content_list + server_tool_blocks if server_tool_blocks else None
            save_result = save_message_to_db(user_id, session_conversation_id, assistant_response_text, "ASSISTANT", request_id, content_type=save_content_type, tool_content=save_tool_content, client_timestamp=final_text_timestamp, local_objects=local_objects)
            if save_result:
                saved_conversation_id, bot_message_id, cancelled_ids = save_result
                logger.info(f"ASSISTANT message saved successfully: conversation_id={saved_conversation_id}, message_id={bot_message_id}")

                # Cancel and broadcast previous messages if any were superseded
                if cancelled_ids:
                    await cancel_and_broadcast_messages(
                        message_ids=cancelled_ids,
                        conversation_id=saved_conversation_id,
                        request_id=request_id,
                        reason="superseded_by_new_message",
                        session_id=session_id
                    )

                # If cancelled, cancel and broadcast
                if request_cancelled:
                    await cancel_and_broadcast_messages(
                        message_ids=[bot_message_id],
                        conversation_id=saved_conversation_id,
                        request_id=request_id,
                        reason="request_cancelled",
                        session_id=session_id
                    )
            else:
                logger.error(f"Failed to save ASSISTANT message for conversation_id={session_conversation_id}, request_id={request_id}")
        else:
            logger.info(f"No text content in Claude's response for request {request_id}, only tool calls. This is normal for tool-only responses.")

        # Check if most recent tool is a pending get_* tool
        # If so, skip broadcast and wait for real result to trigger it
        should_skip_broadcast_for_pending_get = False
        try:
            messages = await get_chat_messages(session_id)
            # Search backwards for the most recent tool_use
            for msg in reversed(messages):
                if msg.get("role") == "assistant":
                    content = msg.get("content", [])
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "tool_use":
                                tool_name = block.get("name", "")
                                tool_use_id = block.get("id")

                                # Check if this is a get_* or agent_* tool
                                if tool_name.startswith("get_"):
                                    # Now check if its tool_result is still pending
                                    for user_msg in reversed(messages):
                                        if user_msg.get("role") == "user":
                                            user_content = user_msg.get("content", [])
                                            if isinstance(user_content, list):
                                                for user_block in user_content:
                                                    if isinstance(user_block, dict) and \
                                                       user_block.get("type") == "tool_result" and \
                                                       user_block.get("tool_use_id") == tool_use_id:
                                                        # Found the tool_result - check if it's pending
                                                        result_content = user_block.get("content", "")
                                                        if result_content == "Result pending...":
                                                            should_skip_broadcast_for_pending_get = True
                                                            logger.info(f"Skipping broadcast for request {request_id}: most recent tool {tool_name} has pending result, waiting for real result to trigger broadcast")
                                                        break
                                        if should_skip_broadcast_for_pending_get:
                                            break
                                # We found the most recent tool_use, stop searching
                                break
                    if should_skip_broadcast_for_pending_get or (msg.get("role") == "assistant" and any(isinstance(b, dict) and b.get("type") == "tool_use" for b in (content if isinstance(content, list) else []))):
                        break
        except Exception as e:
            logger.error(f"Error checking for pending get_* tool: {e}")

        if not request_cancelled and not should_skip_broadcast_for_pending_get and not skip_duplicate_response:
            # Only send response if there's actual text content
            # Empty responses create blank message bubbles in the Android app
            if assistant_response_text and assistant_response_text.strip():
                # Send structured response with request_id and conversation_id
                response_payload = {
                    "response": assistant_response_text,
                    "request_id": request_id,
                    "conversation_id": saved_conversation_id,  # Include real conversation_id for client sync (resolved from optimistic if needed)
                    "client_conversation_id": client_conversation_id,  # Echo back optimistic ID
                    "client_message_id": client_message_id,  # Echo back optimistic message ID
                    "type": "response"  # Indicate this is a direct response (vs broadcast)
                }
                direct_send_succeeded = await safe_websocket_send(response_payload)
                if direct_send_succeeded:
                    logger.info(f"Successfully sent response for request {request_id} to session {session_id}")
                else:
                    logger.info(f"WebSocket send failed but response saved to database: conversation_id={saved_conversation_id}, request_id={request_id}, will be available on reconnect")

                # Broadcast to other WebSocket sessions for the same conversation
                # Use same format as regular response so clients can process it correctly
                broadcast_payload = {
                    "response": assistant_response_text,
                    "request_id": request_id,  # Include request_id so clients can track the message
                    "conversation_id": processing_conversation_id,  # Send the real conversation ID
                    "client_conversation_id": client_conversation_id,  # Include for client validation
                    "client_message_id": client_message_id,  # Include for completeness
                    "type": "broadcast"  # Keep type to indicate it's a broadcast
                }
                # Bot responses should go to ALL sessions - they originate from the server, not from any client session
                # Broadcast using the real conversation ID - the broadcast function will also send to optimistic ID
                # Only exclude session if direct send succeeded - if it failed, include it in broadcast so client can receive on reconnect
                await broadcast_to_conversation(processing_conversation_id, broadcast_payload, exclude_session=session_id if direct_send_succeeded else None)
                logger.info(f"Broadcasted assistant message to all sessions for conversation {processing_conversation_id}")
            else:
                logger.info(f"Skipping empty response broadcast for tool-only response (request {request_id})")

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
        # This is a catch-all for any errors that weren't handled by specific handlers above
        # Should be rare, but ensures user always gets feedback
        logger.error(f"Unexpected error processing request {request_id}: {str(e)}")
        logger.error(f"This error should have been caught by a specific handler - please investigate")

        # Check if this request was already cancelled (e.g., by subset detection)
        request_state = await get_request_state(request_id)
        is_cancelled = request_state and request_state.get("state") == "cancelled"

        if is_cancelled:
            logger.info(f"Request {request_id} was already cancelled - saving error but marking as cancelled")

        await set_request_state(request_id, "failed" if not is_cancelled else "cancelled", {
            "session_id": session_id,
            "conversation_id": session_conversation_id,
            "error": str(e),
            "error_time": time.time()
        })

        # Send error message to user
        error_message = "An unexpected error occurred. Please try again."
        error_payload = {
            "type": "error",
            "code": "UNEXPECTED_ERROR",
            "message": error_message,
            "request_id": request_id,
            "conversation_id": session_conversation_id,
            "client_conversation_id": client_conversation_id,
            "client_message_id": client_message_id
        }

        # Try to save to database
        error_json_content = json.dumps(error_payload)
        local_objects = redis_managers.get("local_objects") if redis_managers else None
        try:
            save_message_to_db(
                user_id=user_id,
                conversation_id=session_conversation_id,
                content=error_json_content,
                message_sender="ASSISTANT",
                request_id=request_id,
                content_type="text",
                mark_cancelled=is_cancelled,  # Mark as cancelled if request was cancelled
                local_objects=local_objects
            )
        except Exception as save_error:
            logger.error(f"Failed to save unexpected error to database: {save_error}")

        # Try to send via WebSocket (only if not cancelled)
        if not is_cancelled:
            await safe_websocket_send(error_payload)
        else:
            logger.info(f"Skipping WebSocket send for cancelled request {request_id}")

        # Don't raise - error has been handled
        return session_conversation_id
        
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