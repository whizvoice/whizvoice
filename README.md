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
