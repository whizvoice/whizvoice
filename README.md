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

5. Install and enable Redis (for WebSocket broadcasting across worker processes):

```bash
# Install Redis
sudo dnf install redis -y

# Start and enable Redis to run on boot
sudo systemctl start redis
sudo systemctl enable redis

# Test Redis is working
redis-cli ping  # Should return PONG
```

6. Set up service

```bash
sudo cp whizvoice.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable whizvoice
sudo systemctl start whizvoice
```

Check the status

```bash
sudo systemctl status whizvoice
# Look for "Successfully connected to Redis for pub/sub" in logs
```

## Running the Chatbot

### nginx and ssl certs

i have my repo in /var/www.

```
sudo ln -s /var/www/whizvoice/whizvoice.com/nginx/whizvoice.bootstrap /etc/nginx/conf.d/whizvoice.com.conf
sudo nginx -t #to make sure nginx conf is valid
sudo service nginx reload
sudo certbot certonly --webroot -w /var/www/whizvoice/whizvoice.com -d whizvoice.com -d www.whizvoice.com
sudo rm /etc/nginx/conf.d/whizvoice.com.conf
sudo ln -s /var/www/whizvoice/whizvoice.com/nginx/whizvoice.com.conf /etc/nginx/conf.d/whizvoice.com.conf
sudo nginx -t #to make sure nginx conf is valid
sudo service nginx reload
```

set up cron job to autorenew ssl cert

```
crontab -e
```

add this to your crontab

```
0 12 * * * /usr/bin/certbot renew --quiet && /usr/bin/systemctl reload nginx
```

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
- **test_execute_tool.py** - Tests for the tool execution dispatcher (20 tests)
- **test_about_me_tool.py** - Tests for app information retrieval (7 tests)
- **test_app_helpers.py** - Tests for authentication and client management (9 tests)
- **test_tool_registry.py** - Tests for the new tool registry system (8 tests)

Current test coverage: **35%** overall with **59 total tests**

Areas with good coverage:

- **about_me_tool.py**: 100% coverage - App information functionality
- **asana_tools.py**: 66% coverage - Asana integration and task management
- **supabase_client.py**: 100% coverage - Database client setup
- **constants.py**: 100% coverage - Application constants

The tests cover:

- Asana API integration (workspaces, tasks, task creation, parent/child relationships)
- User preferences management (setting/getting preferences, encrypted preferences)
- Tool execution system (authentication, parameter validation, error handling)
- Tool registry system (dynamic tool routing, validation, authentication requirements)
- Authentication and client caching
- Error handling and edge cases

## troubleshooting

### websockets

Count all established connections to your WebSocket port (e.g., 8000)

```
netstat -an | grep :8000 | grep ESTABLISHED | wc -l
```

See detailed connections

```
netstat -an | grep :8000 | grep ESTABLISHED
```

*** NOTE THAT WE HAVE A GLOBAL CONNECTIONS LIMIT OF 500 AND A LIMIT OF 5 PER USER ***

## Message Ordering and Timestamp Constraints

To ensure proper conversation history when messages are saved to the database and loaded back, the following constraints MUST be maintained:

### Timestamp Rules for Messages with Same request_id

All messages in a request/response cycle share the same `request_id`. Timestamps must be carefully managed to ensure proper ordering:

1. **Base Rule**: ASSISTANT messages with the same `request_id` need to have timestamps that are +1ms after the USER message timestamp
   - This ensures responses appear immediately after the user message they're responding to

2. **Tool Use Flow with Placeholder tool_result**:
   - When an ASSISTANT message contains tool_use blocks, we create a placeholder tool_result immediately to allow conversation to continue
   - **USER message** (original): timestamp T (e.g., .464)
   - **ASSISTANT text_before** (if any): T+1ms (e.g., .465)
   - **ASSISTANT tool_use**: T+2ms (e.g., .466)
   - **USER placeholder tool_result**: T+3ms (e.g., .467) - with content "Result pending..."
   - **ASSISTANT text_after**: T+4ms (e.g., .468)

3. **Real tool_result Replacement**:
   - When the actual tool execution completes, the real tool_result MUST replace the placeholder
   - **CRITICAL**: The timestamp of the placeholder (T+3ms) MUST be preserved when replacing with the real result
   - This ensures the final ASSISTANT text (T+4ms) remains after the tool_result

4. **Multiple Tool Uses**:
   - If there are additional tool_use and tool_result pairs before the next USER text message, timestamps continue incrementing:
   - Second tool_use: T+5ms
   - Second placeholder tool_result: T+6ms
   - Final ASSISTANT text: T+7ms

### Message Merging Rules

Claude API requires strict user/assistant alternation. Messages must be merged to maintain this:

1. **ASSISTANT Messages with Text and Tool Use**:
   - Text content MUST come before tool_use blocks in the same message
   - All content from the same ASSISTANT turn must be merged into a single message
   - Example: `{"role": "assistant", "content": [{"type": "text", "text": "..."}, {"type": "tool_use", ...}]}`

2. **Consecutive USER Messages**:
   - USER messages in a row (between ASSISTANT messages) MUST be merged together
   - This can happen when multiple user inputs or tool_results arrive before the next ASSISTANT response
   - If a tool_result and text arrive together, tool_result MUST come first, then text
   - Example: `{"role": "user", "content": [{"type": "tool_result", ...}, {"type": "text", "text": "..."}]}`

### Implementation Notes

- The `save_message_to_db()` function in `database.py` handles timestamp management
- The `load_conversation_history()` function in `database.py` handles message merging when loading from database
- Tool messages (tool_use, tool_result) are stored in the database but filtered out when syncing to Android client (which only shows text messages)
- Redis cache maintains the full conversation history including tool messages for server-side Claude API calls
