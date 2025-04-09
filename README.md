# whizvoice

A simple command-line chatbot powered by Claude AI with Asana integration.

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

- On Windows:

```
.\venv\Scripts\activate
```

3. Install requirements:

```
pip install -r requirements.txt
```

4. Create a `constants.py` file with your API keys:

```
CLAUDE_API_KEY = "your-claude-api-key"
ASANA_ACCESS_TOKEN = "your-asana-token"
```

## Running the Chatbot

```
python chat.py
```

## Running Tests

The project includes unit tests for both the Asana integration and chat functionality.

### Running All Tests

```
python -m unittest discover tests
```

### Test Coverage

To run tests with coverage report:

```
# Run tests with coverage
coverage run -m unittest discover tests

# Generate coverage report
coverage report
```
