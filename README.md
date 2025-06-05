# whizvoice

A simple command-line chatbot powered by Claude AI with Asana integration.

## Setup Instructions

1. Create a virtual environment:

```bash
python3 -m venv venv
```

2. Activate the virtual environment:

```bash
source venv/bin/activate
```

3. Install requirements:

```bash
pip install -r requirements.txt
```

4. Set up pre-commit hooks so unit tests run on git commit:

```bash
pre-commit install
```

5. set up service

```bash
sudo cp whizvoice.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable whizvoice
sudo systemctl start whizvoice
```

check the status

```bash
sudo systemctl status whizvoice
```

## Running the Chatbot

### Development server

```
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

`chat.py` is the old script that doesn't have a web server

Run on mac to communicate with the server via websocket

installation

```
brew install websocat
```

connect

```
websocat ws://REDACTED_SERVER_IP:8000/chat
```

### Production server

```
uvicorn app:app --host 0.0.0.0 --port 8000 --workers 4
```

## Running Tests

The project includes unit tests for the Asana integration and preferences functionality.

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

Current test coverage is approximately 75% with tests covering:

- Asana API integration functions
- User preferences management
- Date and timezone handling
- Task creation and management
