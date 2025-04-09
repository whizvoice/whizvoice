from anthropic import Anthropic
from constants import CLAUDE_API_KEY
from asana_tools import tools, get_asana_tasks, get_asana_workspaces
import json

SYSTEM_PROMPT = """You are a friendly assistant that can help with anything. Specifically for conversations related to Asana or tasks, please use the tools provided to answer the user's question, using multiple tools at once if necessary. In this case, please just give the answer and do not reply again to ask clarification questions."""

def send_message_to_claude(client, messages, include_tools=True):
    """Send a message to Claude and get the response"""
    params = {
        "model": "claude-3-7-sonnet-20250219",
        "max_tokens": 1000,
        "messages": messages,
        "system": SYSTEM_PROMPT,
    }
    
    if include_tools:
        params["tools"] = tools
        params["tool_choice"] = {"type": "auto"}
    
    return client.messages.create(**params)

def execute_tool(tool_name, tool_args):
    """Execute a tool and return its result"""
    if tool_name == 'get_asana_workspaces':
        return get_asana_workspaces()
    elif tool_name == 'get_asana_tasks':
        workspace_gid = tool_args.get('workspace_gid')
        return get_asana_tasks(workspace_gid)
    raise ValueError(f"Unknown tool: {tool_name}")

class ChatSession:
    def __init__(self, client):
        self.client = client
        self.messages = []
    
    def handle_message(self, user_input):
        """Process a single message and return the final response"""
        self.messages = [{"role": "user", "content": user_input}]
        message = send_message_to_claude(self.client, self.messages)
        
        while message.stop_reason == 'tool_use':
            tool_block = next(block for block in message.content if block.type == 'tool_use')
            result = execute_tool(tool_block.name, tool_block.input)
            
            self.messages.extend([
                {"role": "assistant", "content": "Using tool: " + tool_block.name},
                {"role": "assistant", "content": f"Tool result: {json.dumps(result)}"}
            ])
            message = send_message_to_claude(self.client, self.messages)
        
        return message

def chat():
    client = Anthropic(api_key=CLAUDE_API_KEY)
    session = ChatSession(client)
    
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
            print("DEBUG: Full error:", e)

if __name__ == "__main__":
    chat()
