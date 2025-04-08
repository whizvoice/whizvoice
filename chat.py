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

def chat():
    # Initialize Claude client
    client = Anthropic(api_key=CLAUDE_API_KEY)
    
    print("Chatbot: Hello! I'm Claude with Asana integration. Type 'quit' to exit.")
    
    while True:
        user_input = input("You: ")
        
        if user_input.lower() == 'quit':
            print("Chatbot: Goodbye!")
            break
            
        try:
            # Start conversation with user input
            messages = [{"role": "user", "content": user_input}]
            message = send_message_to_claude(client, messages)
            
            while message.stop_reason == 'tool_use':
                # Find the ToolUseBlock in the content
                tool_block = next(block for block in message.content if block.type == 'tool_use')
                tool_name = tool_block.name
                tool_args = tool_block.input
                
                print(f"DEBUG: Using tool: {tool_name}")
                if tool_name == 'get_asana_workspaces':
                    result = get_asana_workspaces()
                elif tool_name == 'get_asana_tasks':
                    workspace_gid = tool_args.get('workspace_gid')
                    result = get_asana_tasks(workspace_gid)
                
                # Add tool response to messages
                messages.extend([
                    {"role": "assistant", "content": "Using tool: " + tool_name},  # Changed from message.content[0].text
                    {"role": "assistant", "content": f"Tool result: {json.dumps(result)}"}
                ])
                message = send_message_to_claude(client, messages)
                
            # Get final response from Claude with tool results
            if message.content[0].type == 'text':  # Only print if it's text
                print(f"Chatbot: {message.content[0].text}")
            
        except Exception as e:
            print("Chatbot: Sorry, I encountered an error:", str(e))
            print("DEBUG: Full error:", e)

if __name__ == "__main__":
    chat()
