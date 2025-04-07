from anthropic import Anthropic
from constants import CLAUDE_API_KEY
from asana_tools import tools, get_asana_tasks, get_workspaces
import json

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
            # First message to Claude
            message = client.messages.create(
                model="claude-3-7-sonnet-20250219",
                max_tokens=1000,
                messages=[{"role": "user", "content": user_input}],
                system="You are a friendly assistant that can help with anything. Specifically for conversations related to Asana or tasks, you have access to a list of tools.",
                tools=tools,
                tool_choice={"type": "any"} if any(word in user_input.lower() for word in ['asana', 'task', 'workspace']) else {"type": "auto"}
            )
            
            print("DEBUG: Response type:", message.content[0].type)  # Debug print
            
            # Handle the response based on its type
            if message.content[0].type == 'text':
                print("Chatbot:", message.content[0].text)
            elif message.content[0].type == 'tool_use':
                print("DEBUG: Tool use content:", vars(message.content[0]))
                
                tool_name = message.content[0].name
                tool_args = message.content[0].input  # Changed from parameters to input
                
                print(f"Using tool: {tool_name}")
                if tool_name == 'get_workspaces':
                    result = get_workspaces()
                elif tool_name == 'get_asana_tasks':
                    workspace_gid = tool_args.get('workspace_gid')
                    result = get_asana_tasks(workspace_gid)
                
                print("DEBUG: Got result:", result)
                
                messages = [
                    {"role": "user", "content": user_input},
                    {"role": "assistant", "content": f"Here's what I found: {json.dumps(result)}"}
                ]
                
                # Get final response from Claude with tool results
                message = client.messages.create(
                    model="claude-3-7-sonnet-20250219",
                    max_tokens=1000,
                    messages=messages,
                    system="You have access to an Asana integration tool that can fetch tasks due today."
                )
                print("Chatbot:", message.content[0].text)
            else:
                print("DEBUG: Unexpected response type:", message.content[0].type)
            
        except Exception as e:
            print("Chatbot: Sorry, I encountered an error:", str(e))
            print("DEBUG: Full error:", e)  # Debug print

if __name__ == "__main__":
    chat()
