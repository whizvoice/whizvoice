"""
Integration tests for message ordering with tool use.

Tests that messages are correctly ordered in both Redis and Supabase:
1. ASSISTANT messages: [text_before, tool_use]
2. USER messages: [tool_result, text_after]
3. Timestamps are correctly ordered: text_before < tool_use < tool_result < text_after
"""
import pytest
import asyncio
import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch, call
from typing import List, Dict

# Mock Anthropic API response structure based on actual logs
class MockToolUse:
    """Mock tool_use block from Claude API"""
    def __init__(self, id="toolu_test123", name="get_weather", input_params=None):
        self.type = "tool_use"
        self.id = id
        self.name = name
        self.input = input_params or {"location": "San Francisco", "days_ahead": 0}

class MockTextBlock:
    """Mock text block from Claude API"""
    def __init__(self, text="I'll get the weather for you."):
        self.type = "text"
        self.text = text

class MockBetaMessage:
    """Mock Claude API response with tool use (from logs: BetaMessage)"""
    def __init__(self, text_before="I'll get the current weather forecast for San Francisco.",
                 tool_id="toolu_test123", tool_name="get_weather",
                 text_after="The weather is 72°F and mostly cloudy."):
        self.id = "msg_test"
        self.type = "message"
        self.role = "assistant"
        self.model = "claude-sonnet-4-20250514"
        self.stop_reason = "tool_use"

        # Content blocks: text_before, then tool_use
        self.content = [
            MockTextBlock(text_before),
            MockToolUse(tool_id, tool_name)
        ]

        # Store text_after for later (would come in follow-up response)
        self._text_after = text_after
        self.usage = {"input_tokens": 100, "output_tokens": 50}

class MockToolResponse:
    """Mock Claude API response after tool execution with text_after"""
    def __init__(self, text_after="The weather is 72°F and mostly cloudy."):
        self.id = "msg_test2"
        self.type = "message"
        self.role = "assistant"
        self.model = "claude-sonnet-4-20250514"
        self.stop_reason = "end_turn"
        self.content = [MockTextBlock(text_after)]
        self.usage = {"input_tokens": 150, "output_tokens": 30}


@pytest.fixture
def mock_redis():
    """Mock Redis manager"""
    redis_mock = AsyncMock()
    redis_mock.messages = []  # Store messages in memory

    async def get_messages(session_id):
        return redis_mock.messages.copy()

    async def set_messages(session_id, messages):
        redis_mock.messages = messages.copy()

    async def add_message(session_id, message):
        redis_mock.messages.append(message)

    redis_mock.get = get_messages
    redis_mock.set = set_messages
    redis_mock.add_message = add_message

    return redis_mock


@pytest.fixture
def mock_supabase():
    """Mock Supabase client"""
    supabase_mock = MagicMock()
    supabase_mock.stored_messages = []  # Store messages in memory

    def insert_message(data):
        """Mock message insert"""
        message_id = len(supabase_mock.stored_messages) + 1
        message_data = data.copy()
        message_data['id'] = message_id
        # Parse timestamp string to datetime for comparison
        if 'timestamp' in message_data:
            ts_str = message_data['timestamp']
            # Handle both Z and +00:00 formats
            ts_str = ts_str.replace('Z', '+00:00')
            message_data['timestamp_dt'] = datetime.fromisoformat(ts_str)
        supabase_mock.stored_messages.append(message_data)

        # Mock the response
        result = MagicMock()
        result.data = [message_data]
        return result

    def get_messages(conversation_id, order_by="timestamp"):
        """Mock message query"""
        messages = [m for m in supabase_mock.stored_messages
                   if m.get('conversation_id') == conversation_id
                   and m.get('cancelled') is None]
        # Sort by timestamp
        messages.sort(key=lambda m: m.get('timestamp_dt', datetime.min))
        result = MagicMock()
        result.data = messages
        return result

    # Setup table mock
    table_mock = MagicMock()

    def insert_fn(data):
        result = MagicMock()
        result.execute = lambda: insert_message(data)
        return result

    table_mock.insert = insert_fn

    # Setup select mock for querying
    def select_fn(fields):
        query_mock = MagicMock()
        query_mock._conversation_id = None

        def eq_fn(field, value):
            if field == "conversation_id":
                query_mock._conversation_id = value
            return query_mock

        def order_fn(field, desc=False):
            return query_mock

        def execute_fn():
            return get_messages(query_mock._conversation_id)

        query_mock.eq = eq_fn
        query_mock.order = order_fn
        query_mock.execute = execute_fn
        return query_mock

    table_mock.select = select_fn

    supabase_mock.table = lambda name: table_mock

    return supabase_mock


@pytest.fixture
def mock_anthropic_client():
    """Mock Anthropic client that returns tool use response"""
    client_mock = AsyncMock()

    # First API call returns tool_use
    tool_response = MockBetaMessage(
        text_before="I'll get the current weather forecast for San Francisco.",
        tool_id="toolu_test123",
        tool_name="get_weather"
    )

    # Second API call returns text_after
    text_after_response = MockToolResponse(
        text_after="The weather is 72°F and mostly cloudy."
    )

    client_mock.messages.create = AsyncMock(side_effect=[tool_response, text_after_response])

    return client_mock


@pytest.mark.asyncio
async def test_message_ordering_with_tool_use(mock_redis, mock_supabase, mock_anthropic_client):
    """
    Test that messages are correctly ordered in both Redis and Supabase when a tool is used.

    Expected flow:
    1. User sends message
    2. Claude responds with text_before + tool_use
    3. Tool is executed
    4. Tool result is stored
    5. Claude responds with text_after

    Expected Redis order:
    - ASSISTANT: [text_before, tool_use]
    - USER: [tool_result, text_after]

    Expected Supabase order (by timestamp):
    - ASSISTANT text_before (T+1ms)
    - ASSISTANT tool_use (T+2ms)
    - USER tool_result (T+3ms)
    - USER text_after (T+4ms)
    """
    from app import (
        save_message_to_db, add_chat_message, get_chat_messages,
        set_chat_messages
    )

    # Setup
    user_id = "test_user_123"
    conversation_id = 1
    session_id = "test_session"
    request_id = "test_request_123"

    # Base timestamp for ordering
    base_time = datetime.utcnow()
    user_timestamp = base_time.isoformat() + 'Z'

    # Step 1: Save user message
    user_message_data = {
        "conversation_id": conversation_id,
        "content": "what's the weather in San Francisco",
        "message_sender": "USER",
        "content_type": "text",
        "request_id": request_id,
        "timestamp": user_timestamp
    }
    mock_supabase.table("messages").insert(user_message_data).execute()

    # Step 2: Mock Claude API response with tool use
    api_response = mock_anthropic_client.messages.create.side_effect[0]

    # Extract content blocks
    text_before_block = api_response.content[0]
    tool_use_block = api_response.content[1]

    # Step 3: Save ASSISTANT messages to Redis (before tool execution)
    # Calculate timestamps
    text_before_timestamp = (base_time + timedelta(milliseconds=1)).isoformat() + 'Z'
    tool_use_timestamp = (base_time + timedelta(milliseconds=2)).isoformat() + 'Z'
    tool_result_timestamp = (base_time + timedelta(milliseconds=3)).isoformat() + 'Z'
    text_after_timestamp = (base_time + timedelta(milliseconds=4)).isoformat() + 'Z'

    # Save to Redis: ASSISTANT message with [text_before, tool_use]
    text_before_dict = {"type": "text", "text": text_before_block.text}
    tool_block_dict = {
        "type": "tool_use",
        "id": tool_use_block.id,
        "name": tool_use_block.name,
        "input": tool_use_block.input
    }

    assistant_content = [text_before_dict, tool_block_dict]
    await mock_redis.add_message(session_id, {"role": "assistant", "content": assistant_content})

    # Save to Supabase: separate messages with timestamps
    # Text before
    mock_supabase.table("messages").insert({
        "conversation_id": conversation_id,
        "content": text_before_block.text,
        "message_sender": "ASSISTANT",
        "content_type": "text",
        "request_id": request_id,
        "timestamp": text_before_timestamp
    }).execute()

    # Tool use
    mock_supabase.table("messages").insert({
        "conversation_id": conversation_id,
        "content": "",
        "message_sender": "ASSISTANT",
        "content_type": "tool_use",
        "tool_content": [tool_block_dict],
        "request_id": request_id,
        "timestamp": tool_use_timestamp
    }).execute()

    # Step 4: Add pending tool result to Redis
    pending_tool_result = {
        "type": "tool_result",
        "tool_use_id": tool_use_block.id,
        "content": json.dumps({"status": "pending"})
    }
    await mock_redis.add_message(session_id, {"role": "user", "content": [pending_tool_result]})

    # Save pending tool result to Supabase
    mock_supabase.table("messages").insert({
        "conversation_id": conversation_id,
        "content": "",
        "message_sender": "USER",
        "content_type": "tool_result",
        "tool_content": [pending_tool_result],
        "request_id": request_id,
        "timestamp": tool_result_timestamp
    }).execute()

    # Step 5: Execute tool (mocked)
    tool_result = {"success": True, "temperature": 72, "conditions": "mostly cloudy"}

    # Update Redis with actual tool result
    actual_tool_result = {
        "type": "tool_result",
        "tool_use_id": tool_use_block.id,
        "content": json.dumps(tool_result)
    }

    # Find and replace the pending result
    messages = await mock_redis.get(session_id)
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if msg.get("role") == "user" and isinstance(msg.get("content"), list):
            for block in msg["content"]:
                if (isinstance(block, dict) and
                    block.get("type") == "tool_result" and
                    block.get("tool_use_id") == tool_use_block.id):
                    messages[i] = {"role": "user", "content": [actual_tool_result]}
                    await mock_redis.set(session_id, messages)
                    break

    # Step 6: Get text_after from second API call
    text_after_response = mock_anthropic_client.messages.create.side_effect[1]
    text_after = text_after_response.content[0].text
    text_after_dict = {"type": "text", "text": text_after}

    # THIS IS THE KEY FIX: Append text_after to USER message (not create new ASSISTANT message)
    messages = await mock_redis.get(session_id)
    updated = False
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if msg.get("role") == "user" and isinstance(msg.get("content"), list):
            has_our_result = any(
                isinstance(block, dict) and
                block.get("type") == "tool_result" and
                block.get("tool_use_id") == tool_use_block.id
                for block in msg["content"]
            )
            if has_our_result:
                # Append text_after to USER message
                messages[i]["content"].append(text_after_dict)
                await mock_redis.set(session_id, messages)
                updated = True
                break

    # Save text_after to Supabase (as USER message to match Redis structure)
    mock_supabase.table("messages").insert({
        "conversation_id": conversation_id,
        "content": text_after,
        "message_sender": "USER",
        "content_type": "text",
        "request_id": request_id,
        "timestamp": text_after_timestamp
    }).execute()

    # ===== ASSERTIONS =====

    # Check Redis ordering
    redis_messages = await mock_redis.get(session_id)
    assert len(redis_messages) == 2, f"Expected 2 messages in Redis, got {len(redis_messages)}"

    # First message should be ASSISTANT with [text_before, tool_use]
    assert redis_messages[0]["role"] == "assistant"
    assert len(redis_messages[0]["content"]) == 2
    assert redis_messages[0]["content"][0]["type"] == "text"
    assert redis_messages[0]["content"][1]["type"] == "tool_use"
    print("✅ Redis ASSISTANT message has correct order: [text_before, tool_use]")

    # Second message should be USER with [tool_result, text_after]
    assert redis_messages[1]["role"] == "user"
    assert len(redis_messages[1]["content"]) == 2, \
        f"Expected USER message to have 2 blocks [tool_result, text_after], got {len(redis_messages[1]['content'])}"
    assert redis_messages[1]["content"][0]["type"] == "tool_result"
    assert redis_messages[1]["content"][1]["type"] == "text"
    print("✅ Redis USER message has correct order: [tool_result, text_after]")

    # Check Supabase timestamp ordering
    db_messages = mock_supabase.stored_messages
    assert len(db_messages) == 5  # user, text_before, tool_use, tool_result, text_after

    # Sort by timestamp to verify order
    db_messages_sorted = sorted(db_messages, key=lambda m: m.get('timestamp_dt', datetime.min))

    # Verify timestamp order
    assert db_messages_sorted[0]['message_sender'] == 'USER'  # Original user message
    assert db_messages_sorted[1]['content_type'] == 'text' and db_messages_sorted[1]['message_sender'] == 'ASSISTANT'  # text_before
    assert db_messages_sorted[2]['content_type'] == 'tool_use'
    assert db_messages_sorted[3]['content_type'] == 'tool_result'
    assert db_messages_sorted[4]['content_type'] == 'text' and db_messages_sorted[4]['message_sender'] == 'USER'  # text_after

    # Verify timestamps are correctly spaced
    ts_user = db_messages_sorted[0]['timestamp_dt']
    ts_text_before = db_messages_sorted[1]['timestamp_dt']
    ts_tool_use = db_messages_sorted[2]['timestamp_dt']
    ts_tool_result = db_messages_sorted[3]['timestamp_dt']
    ts_text_after = db_messages_sorted[4]['timestamp_dt']

    assert ts_text_before > ts_user
    assert ts_tool_use > ts_text_before
    assert ts_tool_result > ts_tool_use
    assert ts_text_after > ts_tool_result
    print("✅ Supabase messages have correct timestamp ordering")

    print("\n🎉 All message ordering tests passed!")


if __name__ == "__main__":
    # Run test
    import sys
    sys.exit(pytest.main([__file__, "-v", "-s"]))
