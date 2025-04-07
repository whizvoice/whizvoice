# whizvoice

A simple command-line chatbot powered by Claude AI.

## Setup Instructions

1. Create a virtual environment:

```
python -m venv venv
```

2. Activate the virtual environment:

   - On macOS/Linux:

```
source venv/bin/activate
```

3. Install requirements:

```
pip install -r requirements.txt
```

4. Create a `constants.py` file with your Claude API key:

```
CLAUDE_API_KEY = "your-api-key-here"
```

5. Run the chatbot:

```
python chat.py
```

## Usage

- Type your message and press Enter to get a response from Claude
- Type 'quit' to exit the chatbot
