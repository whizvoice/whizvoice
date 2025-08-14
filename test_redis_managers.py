#!/usr/bin/env python3
"""
Test script to verify Redis managers work correctly
Run this to test your Redis implementation before deploying
"""

import asyncio
import redis.asyncio as redis
from redis_managers import create_managers
import json
import time


async def test_redis_managers():
    """Test all Redis managers"""
    
    print("🔧 Testing Redis Managers...")
    
    # Connect to Redis
    try:
        redis_client = await redis.from_url(
            "redis://localhost:6379",
            encoding="utf-8",
            decode_responses=True
        )
        await redis_client.ping()
        print("✅ Connected to Redis")
    except Exception as e:
        print(f"❌ Failed to connect to Redis: {e}")
        print("Make sure Redis is running: redis-server")
        return
    
    # Create managers
    managers = create_managers(redis_client)
    
    # Test ChatSessionManager
    print("\n📝 Testing ChatSessionManager...")
    session_id = "test_session_123"
    
    # Set initial messages
    messages = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"}
    ]
    await managers["chat_sessions"].set(session_id, messages)
    
    # Get messages
    retrieved = await managers["chat_sessions"].get(session_id)
    assert retrieved == messages, "Messages don't match"
    print(f"  ✅ Set and retrieved {len(messages)} messages")
    
    # Append message
    new_msg = {"role": "user", "content": "How are you?"}
    await managers["chat_sessions"].append(session_id, new_msg)
    retrieved = await managers["chat_sessions"].get(session_id)
    assert len(retrieved) == 3, "Message not appended"
    print("  ✅ Appended message successfully")
    
    # Test UserSessionManager
    print("\n👤 Testing UserSessionManager...")
    user_id = "test_user_456"
    
    # Add sessions
    await managers["user_sessions"].add_session(user_id, "session1")
    await managers["user_sessions"].add_session(user_id, "session2")
    
    # Get sessions
    sessions = await managers["user_sessions"].get_sessions(user_id)
    assert len(sessions) == 2, "Sessions not added"
    print(f"  ✅ Added {len(sessions)} sessions for user")
    
    # Remove session
    await managers["user_sessions"].remove_session(user_id, "session1")
    sessions = await managers["user_sessions"].get_sessions(user_id)
    assert len(sessions) == 1, "Session not removed"
    print("  ✅ Removed session successfully")
    
    # Test SessionTimestampManager
    print("\n⏰ Testing SessionTimestampManager...")
    
    # Update timestamp
    await managers["session_timestamps"].update(session_id)
    timestamp = await managers["session_timestamps"].get(session_id)
    assert timestamp is not None, "Timestamp not set"
    assert abs(timestamp - time.time()) < 1, "Timestamp incorrect"
    print("  ✅ Timestamp tracking working")
    
    # Test ActiveRequestManager
    print("\n📡 Testing ActiveRequestManager...")
    
    # Add requests
    await managers["active_requests"].add(session_id, "req1")
    await managers["active_requests"].add(session_id, "req2")
    
    # Get requests
    requests = await managers["active_requests"].get_all(session_id)
    assert len(requests) == 2, "Requests not added"
    print(f"  ✅ Tracking {len(requests)} active requests")
    
    # Clear requests
    await managers["active_requests"].clear(session_id)
    requests = await managers["active_requests"].get_all(session_id)
    assert len(requests) == 0, "Requests not cleared"
    print("  ✅ Cleared requests successfully")
    
    # Test SessionMappingManager
    print("\n🔄 Testing SessionMappingManager...")
    
    # Set mapping
    client_id = -1
    real_id = 100
    await managers["session_mappings"].set_mapping(session_id, client_id, real_id)
    
    # Get mappings
    retrieved_real = await managers["session_mappings"].get_real_id(session_id, client_id)
    assert retrieved_real == real_id, "Real ID mapping failed"
    
    retrieved_client = await managers["session_mappings"].get_optimistic_id(session_id, real_id)
    assert retrieved_client == client_id, "Client ID mapping failed"
    print("  ✅ ID mappings working correctly")
    
    # ConnectionTracker removed - functionality moved to LocalObjectManager
    print("\n🌐 ConnectionTracker removed - WebSocket tracking now in LocalObjectManager")
    
    # Clean up test data
    print("\n🧹 Cleaning up test data...")
    await managers["chat_sessions"].delete(session_id)
    await managers["user_sessions"].remove_session(user_id, "session2")
    await managers["session_timestamps"].delete(session_id)
    await managers["session_mappings"].delete(session_id)
    
    # Verify cleanup
    exists = await managers["chat_sessions"].exists(session_id)
    assert not exists, "Session not deleted"
    print("  ✅ Cleanup successful")
    
    print("\n✅ All Redis manager tests passed!")
    print("\n🎯 Next steps:")
    print("1. Update your app.py to use these managers")
    print("2. Set REDIS_URL environment variable for production")
    print("3. Deploy with multiple instances behind a load balancer")
    
    # Close Redis connection
    await redis_client.close()


async def test_multi_server_simulation():
    """Simulate multiple servers using Redis"""
    
    print("\n🚀 Testing Multi-Server Simulation...")
    
    redis_client = await redis.from_url(
        "redis://localhost:6379",
        encoding="utf-8",
        decode_responses=True
    )
    
    # Simulate two servers
    server1_managers = create_managers(redis_client)
    server2_managers = create_managers(redis_client)
    
    # Server 1 creates a session
    await server1_managers["chat_sessions"].set(
        "shared_session",
        [{"role": "user", "content": "Hello from server 1"}]
    )
    
    # Server 2 can read it immediately
    messages = await server2_managers["chat_sessions"].get("shared_session")
    assert len(messages) == 1, "Cross-server session sharing failed"
    print("  ✅ Server 2 can read Server 1's session data")
    
    # Server 2 appends a message
    await server2_managers["chat_sessions"].append(
        "shared_session",
        {"role": "assistant", "content": "Response from server 2"}
    )
    
    # Server 1 sees the update
    messages = await server1_managers["chat_sessions"].get("shared_session")
    assert len(messages) == 2, "Cross-server updates not working"
    print("  ✅ Server 1 sees Server 2's updates")
    
    # Clean up
    await server1_managers["chat_sessions"].delete("shared_session")
    
    print("  ✅ Multi-server simulation successful!")
    
    await redis_client.close()


if __name__ == "__main__":
    print("=" * 50)
    print("WhizVoice Redis Manager Test Suite")
    print("=" * 50)
    
    # Run tests
    asyncio.run(test_redis_managers())
    asyncio.run(test_multi_server_simulation())
    
    print("\n" + "=" * 50)
    print("All tests completed successfully! 🎉")
    print("Your Redis managers are ready for production.")
    print("=" * 50)