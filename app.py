from fastapi import FastAPI, WebSocket, HTTPException, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import json
import os
import traceback

from anthropic import Anthropic
from constants import CLAUDE_API_KEY
from asana_tools import tools, get_asana_tasks, get_asana_workspaces, get_current_date, get_parent_tasks, create_asana_task, change_task_parent
from preferences import set_preference, get_preference

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

# Store active chat sessions
chat_sessions = {}

@app.get("/")
async def root():
    return {"message": "Welcome to WhizVoice API"}

@app.websocket("/chat")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    session_id = "ws_" + str(id(websocket))
    chat_sessions[session_id] = []
    
    # Send welcome message
    await websocket.send_text("Hello! I'm Claude with Asana integration.")
    
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
                    result = execute_tool(tool_block.name, tool_block.input)
                    
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
                if session_id in chat_sessions:
                    del chat_sessions[session_id]
                break
            except Exception as e:
                # Handle other errors
                await websocket.send_text(f"Error: {str(e)}")
                await websocket.send_text(f"{traceback.print_exc()}")
                continue
                
    except Exception as e:
        # Handle any other errors that might occur
        if session_id in chat_sessions:
            del chat_sessions[session_id]
        raise

def execute_tool(tool_name, tool_args):
    """Execute a tool and return its result"""
    if tool_name == 'get_asana_workspaces':
        return get_asana_workspaces()
    elif tool_name == 'get_asana_tasks':
        workspace_gid = tool_args.get('workspace_gid')
        start_date = tool_args.get('start_date')
        end_date = tool_args.get('end_date')
        return get_asana_tasks(workspace_gid, start_date, end_date)
    elif tool_name == 'get_current_date':
        return get_current_date()
    elif tool_name == 'set_workspace_preference':
        workspace_gid = tool_args.get('workspace_gid')
        return set_preference('asana_workspace_preference', workspace_gid)
    elif tool_name == 'get_workspace_preference':
        return get_preference('asana_workspace_preference')
    elif tool_name == 'get_parent_tasks':
        workspace_gid = tool_args.get('workspace_gid')
        return get_parent_tasks(workspace_gid)
    elif tool_name == 'create_asana_task':
        name = tool_args.get('name')
        workspace_gid = tool_args.get('workspace_gid')
        due_date = tool_args.get('due_date')
        notes = tool_args.get('notes')
        parent_task_gid = tool_args.get('parent_task_gid')
        return create_asana_task(name, workspace_gid, due_date, notes, parent_task_gid)
    elif tool_name == 'change_task_parent':
        task_gid = tool_args.get('task_gid')
        new_parent_gid = tool_args.get('new_parent_gid')
        return change_task_parent(task_gid, new_parent_gid)
    raise ValueError(f"Unknown tool: {tool_name}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)