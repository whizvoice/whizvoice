from fastapi import FastAPI, WebSocket, HTTPException, WebSocketDisconnect, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import json
import os
import traceback
import logging

from anthropic import Anthropic
from constants import CLAUDE_API_KEY
from asana_tools import tools, get_asana_tasks, get_asana_workspaces, get_current_date, get_parent_tasks, create_asana_task, change_task_parent
from preferences import set_preference, get_preference, ensure_user_and_prefs, get_preference_key, set_preference_key
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

# Initialize Anthropic client
client = Anthropic(api_key=CLAUDE_API_KEY)

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

# Store active chat sessions
chat_sessions = {}

# User sessions mapping - maps user IDs to their chat sessions
user_sessions = {}

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
                    response = client.beta.messages.create(
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
                        
                        response = client.beta.messages.create(
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
    
    if not user_id and tool_name in ["get_asana_tasks", "get_parent_tasks", "create_asana_task", "get_workspace_preference", "set_workspace_preference"]:
        return {"error": f"User authentication required for tool: {tool_name}"}

    if tool_name == "get_asana_workspaces":
        return get_asana_workspaces()
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
        return change_task_parent(task_gid, new_parent_gid)
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