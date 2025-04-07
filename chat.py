from anthropic import Anthropic
from constants import CLAUDE_API_KEY
from asana_tools import tools, get_asana_tasks, get_asana_workspaces
import json

def send_message_to_claude(user_input, system_prompt=None, messages=None, tool_choice={"type": "auto"}):
    """Send a message to Claude and get the response
    
    Args:
        user_input (str): The user's message
        system_prompt (str, optional): System prompt to use. Defaults to None.
        messages (list, optional): Previous messages for context. Defaults to None.
        tool_choice (dict, optional): Tool choice configuration. Defaults to {"type": "auto"}.
    """
    client = Anthropic(api_key=CLAUDE_API_KEY)
    
    # Set default system prompt if none provided
    if system_prompt is None:
        system_prompt = """You are an Asana task management assistant. Your primary function is to help users check their Asana workspaces and tasks.

IMPORTANT: You have two tools available that you MUST use:
1. get_asana_workspaces: Use this for ANY questions about workspaces
2. get_asana_tasks: Use this for ANY questions about tasks or what's due

Do not explain what you're going to do - just use the appropriate tool immediately when asked about workspaces or tasks.

For any other topics, you can chat normally without using tools."""
    
    # Use provided messages or create new message list
    if messages is None:
        messages = [{"role": "user", "content": user_input}]
    
    # Create message parameters
    params = {
        "model": "claude-3-7-sonnet-20250219",
        "max_tokens": 1000,
        "messages": messages,
        "system": system_prompt,
        "tools": tools,
        "tool_choice": tool_choice
    }
    
    return client.messages.create(**params)

def chat():
    """Run the chat interface"""
    print("Chatbot: Hello! I'm Claude with Asana integration. Type 'quit' to exit.")
    
    while True:
        user_input = input("You: ")
        
        if user_input.lower() == 'quit':
            print("Chatbot: Goodbye!")
            break
            
        try:
            # Get response from Claude
            message = send_message_to_claude(user_input)
            
            print("DEBUG: Response type:", message.content[0].type)
            
            # Handle the response based on its type
            if message.content[0].type == 'text':
                print("Chatbot:", message.content[0].text)
            elif message.content[0].type == 'tool_use':
                print("DEBUG: Tool use content:", vars(message.content[0]))
                
                tool_name = message.content[0].name
                tool_args = message.content[0].input
                
                print(f"Using tool: {tool_name}")
                if tool_name == 'get_workspaces':
                    result = get_workspaces()
                elif tool_name == 'get_asana_tasks':
                    workspace_gid = tool_args.get('workspace_gid')
                    result = get_asana_tasks(workspace_gid)
                
                print("DEBUG: Got result:", result)
                
                # Send results back to Claude
                messages = [
                    {"role": "user", "content": user_input},
                    {"role": "assistant", "content": f"Here's what I found: {json.dumps(result)}"}
                ]
                message = send_message_to_claude(
                    user_input,
                    messages=messages
                )
                print("Chatbot:", message.content[0].text)
            else:
                print("DEBUG: Unexpected response type:", message.content[0].type)
            
        except Exception as e:
            print("Chatbot: Sorry, I encountered an error:", str(e))
            print("DEBUG: Full error:", e)

if __name__ == "__main__":
    chat()
