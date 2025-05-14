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
from auth import verify_google_token, create_access_token, get_current_user, AuthError, SECRET_KEY as AUTH_SECRET_KEY, ALGORITHM as AUTH_ALGORITHM

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
        
        # Create a JWT token for our service
        token_data = {
            "sub": user_info["sub"],
            "email": user_info["email"],
            "name": user_info["name"]
        }
        
        access_token = create_access_token(token_data)
        
        return {
            "access_token": access_token,
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
                logger.warning(f"Invalid JWT in WebSocket connection: {str(e)}. Token (first 15 chars): {token[:15]}...")
                await websocket.send_text("Authentication failed. Please login again.")
                await websocket.close(code=1008, reason="Invalid token")
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
                        await websocket.send_text("Error: Claude API key not configured for your account. Please contact support or set your API key via preferences.")
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
                        tool_block = next(block for block in response.content if block.type == 'tool_use')
                        # Pass the authenticated user_id to execute_tool
                        logger.debug(f"Executing tool: {tool_block.name} for user_id: {user_id} with input: {tool_block.input}")
                        result = execute_tool(tool_block.name, tool_block.input, user_id)
                        
                        chat_sessions[session_id].extend([
                            {"role": "assistant", "content": [tool_block]},
                            {"role": "user", "content": [{
                                "type": "tool_result",
                                "tool_use_id": tool_block.id,
                                "content": json.dumps(result)
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
                    
                    # Add assistant response to session
                    chat_sessions[session_id].append({"role": "assistant", "content": response.content})
                    
                    # Send response back to client
                    if response.content[0].type == 'text':
                        await websocket.send_text(response.content[0].text)
                    else:
                        await websocket.send_text("Error: Unexpected response format")
                        
                except WebSocketDisconnect:
                    # Clean up the session
                    cleanup_session(session_id, user_id)
                    break
                except Exception as e:
                    # Handle other errors
                    logger.error(f"WebSocket error: {str(e)}")
                    logger.error(traceback.format_exc())
                    await websocket.send_text(f"Error: {str(e)}")
                    continue
                    
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