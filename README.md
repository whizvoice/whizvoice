# whizvoice

A simple command-line chatbot powered by Claude AI with Asana integration.

## Setup Instructions

1. Create a virtual environment:

```bash
python -m venv venv
```

2. Activate the virtual environment:

```bash
source venv/bin/activate
```

3. Install requirements:

```bash
pip install -r requirements.txt
```

4. Create a `constants.py` file with your API keys:

```python
CLAUDE_API_KEY = "your-claude-api-key"
ASANA_ACCESS_TOKEN = "your-asana-token"
```

5. Set up pre-commit hooks so unit tests run on git commit:

```bash
pre-commit install
```

## Running the Chatbot

```bash
python chat.py
```

## Running Tests

The project includes unit tests for both the Asana integration and chat functionality.

### Running All Tests

```bash
python -m unittest discover tests
```

### Test Coverage

To run tests with coverage report:

```bash
# Run tests with coverage
coverage run -m unittest discover tests
# Generate coverage report
coverage report
```
