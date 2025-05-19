import json
import traceback

from anthropic import Anthropic
from asana_tools import tools, get_asana_tasks, get_asana_workspaces, get_current_date, get_parent_tasks, create_asana_task, change_task_parent
from preferences import set_preference, get_preference, get_encrypted_preference

SYSTEM_PROMPT = """You are a friendly assistant that can help with anything. Specifically for conversations related to Asana or tasks, please use the tools provided to answer the user's question, using multiple tools at once if necessary. In this case, please just give the answer and do not reply again to ask clarification questions."""

def send_message_to_claude(client, messages, include_tools=True):
    """Send a message to Claude and get the response"""
    params = {
        "model": "claude-3-7-sonnet-20250219",
        "max_tokens": 1000,
        "messages": messages,
        "system": SYSTEM_PROMPT,
        "betas": ["token-efficient-tools-2025-02-19"]
    }
    
    if include_tools:
        params["tools"] = tools
        params["tool_choice"] = {"type": "auto"}
    
    return client.beta.messages.create(**params)

def execute_tool(tool_name, tool_args, user_id=None):
    """Execute a tool and return its result"""
    if tool_name == "get_asana_workspaces":
        return get_asana_workspaces()
    elif tool_name == "get_asana_tasks":
        workspace_gid = tool_args.get('workspace_gid')
        start_date = tool_args.get('start_date')
        end_date = tool_args.get('end_date')
        return get_asana_tasks(user_id, start_date, end_date)
    elif tool_name == "get_current_date":
        return get_current_date()
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
            raise ValueError("Workspace GID is required for set_workspace_preference")
        return set_preference(user_id, 'asana_workspace_preference', workspace_gid)
    else:
        raise ValueError(f"Unknown tool: {tool_name}")

class ChatSession:
    def __init__(self, user_id):
        self.user_id = user_id
        self.messages = []
        # Get user's Claude API key
        api_key = get_encrypted_preference(user_id, 'claude_api_key')
        if not api_key:
            raise ValueError("No Claude API key found for user. Please set your Claude API key in settings.")
        self.client = Anthropic(api_key=api_key)
    
    def handle_message(self, user_input):
        """Process a single message and return the final response"""
        if len(self.messages) == 0:
            self.messages = [{"role": "user", "content": user_input}]
        else:
            self.messages.append({"role": "user", "content": user_input})
        message = send_message_to_claude(self.client, self.messages)
            
        while message.stop_reason == 'tool_use':
            tool_block = next(block for block in message.content if block.type == 'tool_use')
            result = execute_tool(tool_block.name, tool_block.input, self.user_id)
            print(f"DEBUG: Using tool: {tool_block.name} with input: {tool_block.input}")
            
            # Add tool use message first
            self.messages.append({
                "role": "assistant",
                "content": [tool_block]
            })
            
            # Then add tool result
            self.messages.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": tool_block.id,
                    "content": json.dumps(result)
                }]
            })
            
            message = send_message_to_claude(self.client, self.messages)
        self.messages.append({"role": "assistant", "content": message.content})
        
        return message

def chat(user_id):
    session = ChatSession(user_id)
    
    print("Chatbot: Hello! I'm Claude with Asana integration. Type 'quit' to exit.")
    
    while True:
        user_input = input("You: ")
        
        if user_input.lower() == 'quit':
            print("Chatbot: Goodbye!")
            break
            
        try:
            message = session.handle_message(user_input)
            if message.content[0].type == 'text':
                print(f"Chatbot: {message.content[0].text}")
        except Exception as e:
            print("Chatbot: Sorry, I encountered an error:", str(e))
            print(f"DEBUG: Full message: {message}")
            traceback.print_exc()

if __name__ == "__main__":
    chat()
