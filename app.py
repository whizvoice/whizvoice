from fastapi import FastAPI, WebSocket, HTTPException, WebSocketDisconnect, Depends, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import json
import os
import traceback
import logging

from anthropic import Anthropic
from asana_tools import tools, get_asana_tasks, get_asana_workspaces, get_current_date, get_parent_tasks, create_asana_task, change_task_parent
from preferences import set_preference, get_preference, ensure_user_and_prefs, get_preference_key, set_preference_key, get_decrypted_preference_key, set_encrypted_preference_key, CLAUDE_API_KEY_PREF_NAME
from auth import verify_google_token, create_access_token, get_current_user, AuthError, SECRET_KEY as AUTH_SECRET_KEY, ALGORITHM as AUTH_ALGORITHM, create_refresh_token

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Log the SECRET_KEY being used by this module instance for WebSocket auth
logger.info(f"App module (WebSocket) AUTH_SECRET_KEY: {AUTH_SECRET_KEY[:5]}...{AUTH_SECRET_KEY[-5:] if len(AUTH_SECRET_KEY) > 10 else ''}")

app = FastAPI(
    title="WhizVoice API",
    description="API for WhizVoice chatbot with Asana integration",
    version="1.0.0"
)

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
class ApiTokenStatusResponse(BaseModel):
    has_claude_token: bool
    has_asana_token: bool

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
        
        # Authenticate if token is present
        user_id = None
        if token:
            try:
                # Verify token
                from jose import jwt, JWTError
                
                logger.debug(f"WebSocket attempting to verify token (first 15 chars): {token[:15]}...")
                payload = jwt.decode(token, AUTH_SECRET_KEY, algorithms=[AUTH_ALGORITHM])
                user_id = payload.get("sub")
                user_email = payload.get("email")
                user_name = payload.get("name", "there")
                
                logger.info(f"Authenticated WebSocket connection for user {user_email} ({user_id})")
                await websocket.send_text(f"Hello {user_name}! I'm Claude with Asana integration.")
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
            # Allow anonymous connections but with warning
            logger.warning("Anonymous WebSocket connection accepted")
            await websocket.send_text("Hello! I'm Claude with Asana integration. For a personalized experience, please login.")
        
        # Create a session ID
        session_id = f"ws_{user_id or id(websocket)}"
        chat_sessions[session_id] = []
        
        # Associate session with user if authenticated
        if user_id:
            if user_id not in user_sessions:
                user_sessions[user_id] = []
            user_sessions[user_id].append(session_id)
        
        try:
            while True:
                try:
                    # Receive message from client
                    message = await websocket.receive_text()
                    
                    # Process message similar to HTTP endpoint
                    chat_sessions[session_id].append({"role": "user", "content": message})

                    current_anthropic_client = get_anthropic_client(user_id)
                    if not current_anthropic_client:
                        error_payload_key = {
                            "type": "error", 
                            "code": "CLAUDE_API_KEY_MISSING", 
                            "message": "Error: Claude API key not configured for your account. Please set it in Settings."
                        }
                        await websocket.send_text(json.dumps(error_payload_key))
                        logger.error(f"Claude client could not be initialized for user {user_id}. API key missing or error fetching.")
                        continue 

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
                            await websocket.send_text(json.dumps({"error": "ServerError", "detail": "Tool use indicated but no tool found."}))
                            raise StopIteration("ToolBlockMissingError")

                        logger.debug(f"Executing tool: {tool_block.name} for user_id: {user_id} with input: {tool_block.input}")
                        tool_execution_result = execute_tool(tool_block.name, tool_block.input, user_id)
                        
                        # Check if the tool_execution_result is our specific Asana auth error
                        if isinstance(tool_execution_result, dict) and \
                           tool_execution_result.get("status_code") == 401 and \
                           "Asana authentication failed" in tool_execution_result.get("error", ""):
                            logger.info(f"Tool {tool_block.name} resulted in Asana auth error. Sending directly to client.")
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
                    
                    # Send Claude's final response back to client
                    if response.content and response.content[0].type == 'text':
                        await websocket.send_text(response.content[0].text)
                    elif response.content: # Response has content, but first block isn't text (e.g. just stop_reason)
                        logger.info("Claude's response did not end with a text block but was not a tool use. Sending a status or nothing.")
                        # Example: send a generic completion message or just log and send nothing to avoid confusing client.
                        # await websocket.send_text(json.dumps({"type": "status", "message": "Processing complete, no text response."}))
                    else: # No content blocks from Claude at all (should be rare)
                        await websocket.send_text(json.dumps({"error": "EmptyResponse", "detail": "Assistant provided no content."}))
                        
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
                    logger.error(f"Error during WebSocket message processing for session {session_id}: {str(e)}")
                    logger.error(traceback.format_exc())
                    try:
                        # Send a structured JSON error to the client
                        error_payload = {
                            "type": "error", 
                            "code": "SERVER_PROCESSING_ERROR", 
                            "message": f"An error occurred: {str(e)}"
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
    
    if user_id and user_id in user_sessions:
        if session_id in user_sessions[user_id]:
            user_sessions[user_id].remove(session_id)
            
        # Clean up empty user sessions list
        if not user_sessions[user_id]:
            del user_sessions[user_id]

def execute_tool(tool_name, tool_args, user_id: Optional[str] = None):
    """Execute a tool and return its result"""
    logger.info(f"Executing tool: {tool_name} with args: {tool_args} for user_id: {user_id}")
    
    if not user_id and tool_name in ["get_asana_tasks", "get_parent_tasks", "create_asana_task", "get_workspace_preference", "set_workspace_preference", "get_asana_workspaces", "change_task_parent"]:
        return {"error": f"User authentication required for tool: {tool_name}"}

    if tool_name == "get_asana_workspaces":
        return get_asana_workspaces(user_id)
    elif tool_name == "get_asana_tasks":
        workspace_gid = tool_args.get('workspace_gid')
        start_date = tool_args.get('start_date')
        end_date = tool_args.get('end_date')
        return get_asana_tasks(user_id, workspace_gid, start_date, end_date)
    elif tool_name == "get_current_date":
        return get_current_date()
    elif tool_name == "get_parent_tasks":
        workspace_gid = tool_args.get('workspace_gid')
        return get_parent_tasks(user_id, workspace_gid)
    elif tool_name == "create_asana_task":
        name = tool_args.get('name')
        workspace_gid = tool_args.get('workspace_gid')
        due_date = tool_args.get('due_date')
        notes = tool_args.get('notes')
        parent_task_gid = tool_args.get('parent_task_gid')
        if not name:
            return {"error": "Task name is required."}
        return create_asana_task(user_id, name, workspace_gid, due_date, notes, parent_task_gid)
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
    # Add cases for get_preference_key and set_preference_key if they are actual tool names
    # Example if 'get_user_preference_key' is a tool name:
    # elif tool_name == 'get_user_preference_key':
    #     if not user_id:
    #         raise ValueError("User context required")
    #     key = tool_args.get('key')
    #     return get_preference_key(user_id, key)
    
    logger.error(f"Unknown tool requested: {tool_name}")
    raise ValueError(f"Unknown tool: {tool_name}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)