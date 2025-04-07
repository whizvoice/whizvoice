from anthropic import Anthropic
from constants import CLAUDE_API_KEY

def chat():
    # Initialize Claude client
    client = Anthropic(api_key=CLAUDE_API_KEY)
    
    print("Chatbot: Hello! I'm Claude. Type 'quit' to exit.")
    
    while True:
        user_input = input("You: ")
        
        if user_input.lower() == 'quit':
            print("Chatbot: Goodbye!")
            break
            
        # Get response from Claude
        try:
            message = client.messages.create(
                model="claude-3-sonnet-20240229",
                max_tokens=1000,
                messages=[{
                    "role": "user",
                    "content": user_input
                }]
            )
            print("Chatbot:", message.content[0].text)
        except Exception as e:
            print("Chatbot: Sorry, I encountered an error:", str(e))

if __name__ == "__main__":
    chat()
