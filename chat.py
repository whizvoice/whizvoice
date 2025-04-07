def chat():
    print("Chatbot: Hello! I'm a simple bot that always agrees. Type 'quit' to exit.")
    
    while True:
        user_input = input("You: ")
        
        if user_input.lower() == 'quit':
            print("Chatbot: Goodbye!")
            break
            
        print("Chatbot: Yes")

if __name__ == "__main__":
    chat()
