from fastapi import FastAPI, WebSocket, HTTPException, WebSocketDisconnect, Depends, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from typing import List, Optional, Dict, Any, Union
import json
import os
import traceback
import logging
import time
from fastapi.responses import JSONResponse

from anthropic import Anthropic, AuthenticationError
from asana_tools import asana_tools, get_asana_tasks, get_asana_workspaces, get_current_date, get_parent_tasks, create_asana_task, change_task_parent
from preferences import set_preference, get_preference, ensure_user_and_prefs, get_decrypted_preference_key, set_encrypted_preference_key, CLAUDE_API_KEY_PREF_NAME, set_user_timezone
from auth import verify_google_token, create_access_token, get_current_user, AuthError, SECRET_KEY as AUTH_SECRET_KEY, ALGORITHM as AUTH_ALGORITHM, create_refresh_token

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# can concatenate additional tools here if needed
tools = asana_tools

app = FastAPI(
    title="WhizVoice API",
    description="API for WhizVoice chatbot with Asana integration",
    version="1.0.0"
)

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
_anthropic_clients_cache: Dict[str, Anthropic] = {}

def get_anthropic_client(user_id: Optional[str]) -> Optional[Anthropic]:
    api_key = get_current_claude_api_key(user_id)
    if not api_key:
        return None

    if api_key in _anthropic_clients_cache:
        return _anthropic_clients_cache[api_key]
    
    logger.info(f"Creating new Anthropic client for user {user_id} (key ending with ...{api_key[-4:] if len(api_key) > 4 else ''}).")
    new_client = Anthropic(api_key=api_key)
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

class MessageCreate(BaseModel):
    conversation_id: int
    content: str
    message_type: str  # 'USER' or 'ASSISTANT'

class MessageResponse(BaseModel):
    id: int
    conversation_id: int
    content: str
    message_type: str
    timestamp: str

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

# Store active chat sessions
chat_sessions = {}

# User sessions mapping - maps user IDs to their chat sessions
user_sessions = {}

# Define the response model for the new GET endpoint
ASANA_ACCESS_TOKEN_PREF_NAME = "asana_access_token" # Define this constant

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
        # For refresh token, only sub is strictly needed for stateless, but can include email for logging/context
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
        if "conversation_id" in websocket.query_params:
            try:
                conversation_id = int(websocket.query_params["conversation_id"])
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
                
                # Load conversation history and initialize session
                # Create a unique session ID per conversation, not just per user
                if conversation_id is not None:
                    session_id = f"ws_{user_id}_conv_{conversation_id}"
                else:
                    # If no specific conversation, create a session for a new conversation
                    session_id = f"ws_{user_id}_new_{int(time.time())}"
                
                conversation_history = load_conversation_history(user_id, conversation_id)
                chat_sessions[session_id] = conversation_history
                
                logger.info(f"Created session {session_id} with {len(conversation_history)} messages")
                
                # Track the current conversation_id for this session
                session_conversation_id = conversation_id
                if conversation_id is None and conversation_history:
                    # We loaded the most recent conversation, but we need to know its ID
                    # Let's get it from the database again
                    from supabase_client import supabase
                    conv_result = supabase.table("conversations").select("id").eq("user_id", user_id).order("last_message_time", desc=True).limit(1).execute()
                    if conv_result.data:
                        session_conversation_id = conv_result.data[0]["id"]
                        # Update session_id to include the found conversation_id
                        new_session_id = f"ws_{user_id}_conv_{session_conversation_id}"
                        chat_sessions[new_session_id] = chat_sessions[session_id]
                        del chat_sessions[session_id]
                        session_id = new_session_id
                
                # Only send welcome message if no conversation history exists
                if not conversation_history:
                    await websocket.send_text(f"Hello {user_name}! I'm Claude with Asana integration.")
                else:
                    logger.info(f"Loaded {len(conversation_history)} messages from conversation history for user {user_id}")
                    
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
        if user_id not in user_sessions:
            user_sessions[user_id] = []
        user_sessions[user_id].append(session_id)
        
        try:
            while True:
                try:
                    # Receive message from client
                    message_text = await websocket.receive_text()
                    
                    # Parse incoming message - support both structured JSON and legacy plain text
                    request_id = None
                    try:
                        message_data = json.loads(message_text)
                        message = message_data.get("message", "")
                        request_id = message_data.get("request_id")
                        logger.info(f"Received structured message with request_id: {request_id}")
                    except json.JSONDecodeError:
                        # Fallback for legacy plain text messages
                        message = message_text
                        logger.info("Received legacy plain text message")
                    
                    logger.info(f"Processing message in session {session_id}, conversation {session_conversation_id}, context length: {len(chat_sessions[session_id])}")
                    
                    # Save user message to database and update session_conversation_id
                    logger.info(f"About to save user message. Current session_conversation_id: {session_conversation_id}")
                    session_conversation_id = save_message_to_db(user_id, session_conversation_id, message, "USER")
                    logger.info(f"After saving user message. Updated session_conversation_id: {session_conversation_id}")
                    if session_conversation_id is None:
                        logger.error("Failed to save user message to database")
                        error_payload = {
                            "type": "error",
                            "code": "DATABASE_ERROR",
                            "message": "Failed to save message to database",
                            "request_id": request_id
                        }
                        await websocket.send_text(json.dumps(error_payload))
                        continue

                    chat_sessions[session_id].append({"role": "user", "content": message})

                    current_anthropic_client = get_anthropic_client(user_id)
                    if not current_anthropic_client:
                        error_payload_key = {
                            "type": "error", 
                            "code": "CLAUDE_API_KEY_MISSING",
                            "message": "Claude API key is not set. Please configure it in settings.",
                            "request_id": request_id
                        }
                        await websocket.send_text(json.dumps(error_payload_key))
                        logger.warning(f"User {user_id} attempted to send message without Claude API key.")
                        continue # Allow user to set key and try again without breaking connection.

                    response = current_anthropic_client.beta.messages.create(
                        model="claude-3-7-sonnet-20250219",
                        max_tokens=1000,
                        messages=chat_sessions[session_id],
                        system="You are a friendly assistant that can help with anything. Specifically for conversations related to Asana or tasks, please use the tools provided to answer the user's question, using multiple tools at once if necessary.",
                        tools=tools,
                        tool_choice={"type": "auto"},
                        betas=["token-efficient-tools-2025-02-19"]
                    )
                    
                    # Handle tool calls
                    while response.stop_reason == 'tool_use':
                        tool_block = next((block for block in response.content if block.type == 'tool_use'), None)
                        if not tool_block:
                            logger.error("Stop reason is tool_use but no tool_use block found.")
                            # Send some error or break, as this is an unexpected state
                            error_payload = {
                                "error": "ServerError", 
                                "detail": "Tool use indicated but no tool found.",
                                "request_id": request_id
                            }
                            await websocket.send_text(json.dumps(error_payload))
                            raise StopIteration("ToolBlockMissingError")

                        logger.debug(f"Executing tool: {tool_block.name} for user_id: {user_id} with input: {tool_block.input}")
                        tool_execution_result = execute_tool(tool_block.name, tool_block.input, user_id)
                        
                        # Check if the tool_execution_result is our specific Asana auth error
                        if isinstance(tool_execution_result, dict) and \
                           tool_execution_result.get("status_code") == 401 and \
                           "Asana authentication failed" in tool_execution_result.get("error", ""):
                            logger.info(f"Tool {tool_block.name} resulted in Asana auth error. Sending directly to client.")
                            # Add request_id to Asana error response
                            tool_execution_result["request_id"] = request_id
                            await websocket.send_text(json.dumps(tool_execution_result))
                            raise StopIteration("AsanaAuthErrorHandled") # Signal to skip normal response

                        # If not the specific Asana auth error, proceed as before with Claude:
                        chat_sessions[session_id].extend([
                            {"role": "assistant", "content": [tool_block]},
                            {"role": "user", "content": [{
                                "type": "tool_result",
                                "tool_use_id": tool_block.id,
                                "content": json.dumps(tool_execution_result) 
                            }]}
                        ])
                        
                        response = current_anthropic_client.beta.messages.create(
                            model="claude-3-7-sonnet-20250219",
                            max_tokens=1000,
                            messages=chat_sessions[session_id],
                            system="You are a friendly assistant that can help with anything. Specifically for conversations related to Asana or tasks, please use the tools provided to answer the user's question, using multiple tools at once if necessary.",
                            tools=tools,
                            tool_choice={"type": "auto"},
                            betas=["token-efficient-tools-2025-02-19"]
                        )
                    
                    # Add assistant final response to session history (if not an intercepted error)
                    chat_sessions[session_id].append({"role": "assistant", "content": response.content})
                    
                    # Extract and save assistant response to database
                    assistant_response_text = ""
                    if response.content and response.content[0].type == 'text':
                        assistant_response_text = response.content[0].text
                        
                        # Save assistant message to database
                        logger.info(f"About to save assistant message to conversation {session_conversation_id}")
                        save_message_to_db(user_id, session_conversation_id, assistant_response_text, "ASSISTANT")
                        
                        # Send structured response with request_id
                        response_payload = {
                            "response": assistant_response_text,
                            "request_id": request_id
                        }
                        await websocket.send_text(json.dumps(response_payload))
                    elif response.content: 
                        logger.info("Claude's response did not end with a text block but was not a tool use. Sending a status or nothing.")
                    else: 
                        error_payload = {
                            "error": "EmptyResponse", 
                            "detail": "Assistant provided no content.",
                            "request_id": request_id
                        }
                        await websocket.send_text(json.dumps(error_payload))

                        
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
                    cleanup_session(session_id, user_id)
                    logger.info(f"WebSocket disconnected for session {session_id}")
                    break
                except Exception as e:
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
                    continue # Attempt to recover and wait for next message if possible
                    
        except Exception as e:
            # Handle any other errors that might occur
            cleanup_session(session_id, user_id)
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

def cleanup_session(session_id: str, user_id: Optional[str] = None):
    """Clean up a session when a WebSocket disconnects"""
    if session_id in chat_sessions:
        del chat_sessions[session_id]
        logger.info(f"Cleaned up chat session: {session_id}")
    
    if user_id and user_id in user_sessions:
        if session_id in user_sessions[user_id]:
            user_sessions[user_id].remove(session_id)
            logger.info(f"Removed session {session_id} from user {user_id} sessions")
            
        # Clean up empty user sessions list
        if not user_sessions[user_id]:
            del user_sessions[user_id]
            logger.info(f"Cleaned up empty user sessions for user {user_id}")

def load_conversation_history(user_id: str, conversation_id: Optional[int] = None) -> List[Dict]:
    """Load conversation history from database and convert to Claude message format"""
    try:
        from supabase_client import supabase
        
        # If no conversation_id specified, get the most recent conversation for the user
        if conversation_id is None:
            conv_result = supabase.table("conversations").select("id").eq("user_id", user_id).order("last_message_time", desc=True).limit(1).execute()
            if not conv_result.data:
                logger.info(f"No existing conversations found for user {user_id}")
                return []
            conversation_id = conv_result.data[0]["id"]
            logger.info(f"Loading most recent conversation {conversation_id} for user {user_id}")
        else:
            # Verify user owns the specified conversation
            conv_result = supabase.table("conversations").select("id").eq("id", conversation_id).eq("user_id", user_id).execute()
            if not conv_result.data:
                logger.warning(f"Conversation {conversation_id} not found or not owned by user {user_id}")
                return []
            logger.info(f"Loading specified conversation {conversation_id} for user {user_id}")
        
        # Get messages for the conversation
        result = supabase.table("messages").select("*").eq("conversation_id", conversation_id).order("timestamp", desc=False).execute()
        
        # Convert database messages to Claude format
        claude_messages = []
        for row in result.data:
            message_role = "user" if row["message_type"] == "USER" else "assistant"
            claude_messages.append({
                "role": message_role,
                "content": row["content"]
            })
        
        logger.info(f"Loaded {len(claude_messages)} messages from conversation {conversation_id} for user {user_id}")
        return claude_messages
        
    except Exception as e:
        logger.error(f"Error loading conversation history for user {user_id}, conversation {conversation_id}: {str(e)}")
        return []

def save_message_to_db(user_id: str, conversation_id: Optional[int], content: str, message_type: str) -> Optional[int]:
    """Save a message to the database and return the conversation_id"""
    try:
        from supabase_client import supabase
        
        logger.info(f"save_message_to_db called: user_id={user_id}, conversation_id={conversation_id}, message_type={message_type}, content='{content[:50]}...'")
        
        # If no conversation_id provided, create a new conversation
        if conversation_id is None:
            logger.warning(f"Creating NEW conversation for user {user_id} because conversation_id is None")
            # Create a new conversation
            conv_result = supabase.table("conversations").insert({
                "user_id": user_id,
                "title": content[:50] + "..." if len(content) > 50 else content,  # Use first part of message as title
                "source": "app"
            }).execute()
            
            if not conv_result.data:
                logger.error(f"Failed to create new conversation for user {user_id}")
                return None
                
            conversation_id = conv_result.data[0]["id"]
            logger.warning(f"Created NEW conversation {conversation_id} for user {user_id}")
        else:
            logger.info(f"Using existing conversation {conversation_id} for user {user_id}")
        
        # Save the message
        result = supabase.table("messages").insert({
            "conversation_id": conversation_id,
            "content": content,
            "message_type": message_type
        }).execute()
        
        if not result.data:
            logger.error(f"Failed to save message to conversation {conversation_id}")
            return None
        
        # Update conversation last_message_time
        supabase.table("conversations").update({
            "last_message_time": "now()"
        }).eq("id", conversation_id).execute()
        
        logger.info(f"Successfully saved {message_type} message to conversation {conversation_id}")
        return conversation_id
        
    except Exception as e:
        logger.error(f"Error saving message to database: {str(e)}")
        return None

# TODO: tool names and call code should be programmatically linked in a dictionary or something
def execute_tool(tool_name, tool_args, user_id: Optional[str] = None):
    """Execute a tool and return its result"""
    logger.info(f"Executing tool: {tool_name} with args: {tool_args} for user_id: {user_id}")
    
    if not user_id and tool_name in ["get_asana_tasks", "get_parent_tasks", "create_asana_task", "get_workspace_preference", "set_workspace_preference", "get_asana_workspaces", "change_task_parent"]:
        return {"error": f"User authentication required for tool: {tool_name}"}

    if tool_name == "get_asana_workspaces":
        return get_asana_workspaces(user_id)
    elif tool_name == "get_asana_tasks":
        start_date = tool_args.get('start_date')
        end_date = tool_args.get('end_date')
        return get_asana_tasks(user_id, start_date, end_date)
    elif tool_name == "get_current_date":
        return get_current_date(user_id)
    elif tool_name == "get_parent_tasks":
        return get_parent_tasks(user_id)
    elif tool_name == "create_asana_task":
        name = tool_args.get('name')
        due_date = tool_args.get('due_date')
        notes = tool_args.get('notes')
        parent_task_gid = tool_args.get('parent_task_gid')
        if not name:
            return {"error": "Task name is required."}
        return create_asana_task(user_id, name, due_date, notes, parent_task_gid)
    elif tool_name == "set_workspace_preference":
        workspace_gid = tool_args.get('workspace_gid')
        if not workspace_gid:
            logger.error("Workspace GID is required for set_workspace_preference")
            raise ValueError("Workspace GID is required for set_workspace_preference")
        return set_preference(user_id, 'asana_workspace_preference', workspace_gid)
    elif tool_name == "get_workspace_preference":
        if not user_id:
            logger.error("User ID is required for get_workspace_preference but not provided.")
            raise ValueError("User context required for get_workspace_preference")
        return get_preference(user_id, 'asana_workspace_preference')
    elif tool_name == "change_task_parent":
        task_gid = tool_args.get('task_gid')
        new_parent_gid = tool_args.get('new_parent_gid')
        return change_task_parent(user_id, task_gid, new_parent_gid)
    
    logger.error(f"Unknown tool requested: {tool_name}")
    raise ValueError(f"Unknown tool: {tool_name}")

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

# ================== CONVERSATION ENDPOINTS ==================

@app.get("/conversations", response_model=List[ConversationResponse])
async def get_conversations(current_user: Dict = Depends(get_current_user)):
    """Get all conversations for the authenticated user, ordered by last_message_time DESC"""
    user_id = current_user.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        from supabase_client import supabase
        
        result = supabase.table("conversations").select("*").eq("user_id", user_id).order("last_message_time", desc=True).execute()
        
        conversations = []
        for row in result.data:
            conversations.append(ConversationResponse(
                id=row["id"],
                user_id=row["user_id"],
                title=row["title"],
                created_at=row["created_at"],
                last_message_time=row["last_message_time"],
                source=row["source"],
                google_session_id=row.get("google_session_id")
            ))
        
        return conversations
    except Exception as e:
        logger.error(f"Error getting conversations for user {user_id}: {str(e)}")
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
        from supabase_client import supabase
        
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
            google_session_id=row.get("google_session_id")
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
        from supabase_client import supabase
        
        result = supabase.table("conversations").select("*").eq("id", conversation_id).eq("user_id", user_id).execute()
        
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
            google_session_id=row.get("google_session_id")
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
        from supabase_client import supabase
        
        # Build update dict
        updates = {}
        if update_data.title is not None:
            updates["title"] = update_data.title
        
        if not updates:
            # No updates provided, just return current conversation
            return await get_conversation(conversation_id, current_user)
        
        result = supabase.table("conversations").update(updates).eq("id", conversation_id).eq("user_id", user_id).execute()
        
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
            google_session_id=row.get("google_session_id")
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating conversation {conversation_id} for user {user_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to update conversation")

@app.delete("/conversations")
async def delete_all_conversations(current_user: Dict = Depends(get_current_user)):
    """Delete all conversations for the authenticated user"""
    user_id = current_user.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        from supabase_client import supabase
        
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
        from supabase_client import supabase
        
        result = supabase.table("conversations").update({
            "last_message_time": "now()"
        }).eq("id", conversation_id).eq("user_id", user_id).execute()
        
        if not result.data:
            raise HTTPException(status_code=404, detail="Conversation not found")
        
        return {"message": "Last message time updated"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating last message time for conversation {conversation_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to update last message time")

# ================== MESSAGE ENDPOINTS ==================

@app.get("/conversations/{conversation_id}/messages", response_model=List[MessageResponse])
async def get_messages(
    conversation_id: int,
    current_user: Dict = Depends(get_current_user)
):
    """Get all messages for a conversation (user must own the conversation)"""
    user_id = current_user.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        from supabase_client import supabase
        
        # First verify user owns the conversation
        conv_result = supabase.table("conversations").select("id").eq("id", conversation_id).eq("user_id", user_id).execute()
        if not conv_result.data:
            raise HTTPException(status_code=404, detail="Conversation not found")
        
        # Get messages for the conversation
        result = supabase.table("messages").select("*").eq("conversation_id", conversation_id).order("timestamp", desc=False).execute()
        
        messages = []
        for row in result.data:
            messages.append(MessageResponse(
                id=row["id"],
                conversation_id=row["conversation_id"],
                content=row["content"],
                message_type=row["message_type"],
                timestamp=row["timestamp"]
            ))
        
        return messages
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
        from supabase_client import supabase
        
        # First verify user owns the conversation
        conv_result = supabase.table("conversations").select("id").eq("id", message.conversation_id).eq("user_id", user_id).execute()
        if not conv_result.data:
            raise HTTPException(status_code=404, detail="Conversation not found")
        
        # Create the message
        result = supabase.table("messages").insert({
            "conversation_id": message.conversation_id,
            "content": message.content,
            "message_type": message.message_type
        }).execute()
        
        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to create message")
        
        # Update conversation last_message_time
        supabase.table("conversations").update({
            "last_message_time": "now()"
        }).eq("id", message.conversation_id).execute()
        
        row = result.data[0]
        return MessageResponse(
            id=row["id"],
            conversation_id=row["conversation_id"],
            content=row["content"],
            message_type=row["message_type"],
            timestamp=row["timestamp"]
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
        from supabase_client import supabase
        
        # First verify user owns the conversation
        conv_result = supabase.table("conversations").select("id").eq("id", conversation_id).eq("user_id", user_id).execute()
        if not conv_result.data:
            raise HTTPException(status_code=404, detail="Conversation not found")
        
        # Get message count
        result = supabase.table("messages").select("id", count="exact").eq("conversation_id", conversation_id).execute()
        
        return {"count": result.count}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting message count for conversation {conversation_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to get message count")

@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
async def catch_all(path: str, request: Request):
    print(f"Unmatched request: {request.method} /{path}")
    return JSONResponse({"error": "Not found"}, status_code=404)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)