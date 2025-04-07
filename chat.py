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
                system="You have access to a tool called 'get_tasks' that you MUST use whenever users ask about Asana tasks or tasks due today. Do not try to answer Asana-related questions without using this tool first.",
                tools=tools
            )
            
            # Print initial response
            print("Chatbot:", message.content[0].text)
            
            # Add debug prints
            print("DEBUG: Content type:", message.content[0].type)
            if hasattr(message.content[0], 'tool_calls'):
                print("DEBUG: Has tool_calls")
                print("DEBUG: Tool calls:", message.content[0].tool_calls)
            
            # Handle tool calls if any
            if hasattr(message.content[0], 'tool_calls') and message.content[0].tool_calls:
                for tool_call in message.content[0].tool_calls:
                    print("DEBUG: Tool call name:", tool_call.name)
                    if tool_call.name == 'get_asana_tasks':
                        print("Fetching Asana tasks...")
                        tasks = get_asana_tasks()
                        
                        # Add tool response to messages
                        messages = [
                            {"role": "user", "content": user_input},
                            {"role": "assistant", "content": message.content[0].text},
                            {"role": "tool", "name": "get_asana_tasks", "content": json.dumps(tasks)}
                        ]
                        
                        # Get final response from Claude with tool results
                        message = client.messages.create(
                            model="claude-3-7-sonnet-20250219",
                            max_tokens=1000,
                            messages=messages,
                            system="You have access to an Asana integration tool that can fetch tasks due today."
                        )
                        print("Chatbot:", message.content[0].text)
            
        except Exception as e:
            print("Chatbot: Sorry, I encountered an error:", str(e))

if __name__ == "__main__":
    chat()
