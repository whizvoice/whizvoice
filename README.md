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

The project includes comprehensive unit tests covering the core functionality.

### Running All Tests

```bash
python3 -m unittest discover tests
```

### Running Tests with Verbose Output

```bash
python3 -m unittest discover tests -v
```

### Test Coverage

To run tests with coverage report:

```bash
# Run tests with coverage
coverage run --source=. -m unittest discover tests
# Generate coverage report
coverage report --omit='tests/*'
```

### Test Structure

The test suite includes:

- **test_asana_tools.py** - Tests for Asana API integration functions (12 tests)
- **test_preferences.py** - Tests for user preferences management (3 tests)
- **test_execute_tool.py** - Tests for the tool execution dispatcher (22 tests)
- **test_about_me_tool.py** - Tests for app information retrieval (7 tests)
- **test_app_helpers.py** - Tests for authentication and client management (9 tests)

Current test coverage: **35%** overall with **51 total tests**

Areas with good coverage:

- **about_me_tool.py**: 100% coverage - App information functionality
- **asana_tools.py**: 66% coverage - Asana integration and task management
- **supabase_client.py**: 100% coverage - Database client setup
- **constants.py**: 100% coverage - Application constants

The tests cover:

- Asana API integration (workspaces, tasks, task creation, parent/child relationships)
- User preferences management (setting/getting preferences, encrypted preferences)
- Tool execution system (authentication, parameter validation, error handling)
- Authentication and client caching
- Error handling and edge cases
