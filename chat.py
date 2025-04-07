from anthropic import Anthropic
from constants import CLAUDE_API_KEY
from asana_tools import tools, get_asana_tasks
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
                system="You have access to a tool called 'get_asana_tasks' that you MUST use whenever users ask about Asana tasks or tasks due today.",
                tools=tools,
                tool_choice={"type": "tool", "name": "get_asana_tasks"} if 'asana' in user_input.lower() else None
            )
            
            print("DEBUG: Response type:", message.content[0].type)  # Debug print
            
            # Handle the response based on its type
            if message.content[0].type == 'text':
                print("Chatbot:", message.content[0].text)
            elif message.content[0].type == 'tool_use':
                print("Fetching Asana tasks...")
                tasks = get_asana_tasks()
                print("DEBUG: Got tasks:", tasks)
                
                # Add tool response to messages - using assistant role instead of tool
                messages = [
                    {"role": "user", "content": user_input},
                    {"role": "assistant", "content": f"Here are the tasks I found: {json.dumps(tasks)}"}
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
