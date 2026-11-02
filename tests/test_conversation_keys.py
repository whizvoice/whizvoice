"""Key formats for live-conversation state, all derived from client_chat_id."""
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import conversation_keys


def test_session_id_format():
    assert conversation_keys.session_id("user-1", "abc-123") == "ws_user-1_conv_abc-123"


def test_session_id_legacy_negative_chat_id():
    assert conversation_keys.session_id("user-1", "-1793565083660") == "ws_user-1_conv_-1793565083660"


def test_session_id_is_stable_for_same_inputs():
    assert conversation_keys.session_id("u", "c") == conversation_keys.session_id("u", "c")


def test_channel_format():
    assert conversation_keys.channel("abc-123") == "conversation:abc-123"


def test_pending_tools_key_format():
    assert conversation_keys.pending_tools_key("abc-123") == "conversation:abc-123:pending_tools"


def test_lock_key_format():
    assert conversation_keys.lock_key("abc-123") == "conversation_lock:abc-123"


def test_connection_session_id_format():
    assert conversation_keys.connection_session_id("user-1", "c0ffee") == "ws_user-1_conn_c0ffee"


# --- Wiring: live keys are built only from client_chat_id ---

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import app as app_module
from redis_managers import ChatSessionManager, LocalObjectManager, create_managers

UUID = "7f3a0c2e-1b4d-4c55-9a77-0d1e2f3a4b5c"


class TestConversationIdForChat(unittest.TestCase):
    @patch("app.conversation_id_for", return_value=19400)
    def test_uuid_resolves_through_row(self, mock_lookup):
        self.assertEqual(app_module.conversation_id_for_chat(UUID, "u"), 19400)
        mock_lookup.assert_called_once_with(UUID, "u")

    @patch("app.conversation_id_for", return_value=None)
    def test_uuid_without_row_is_none(self, mock_lookup):
        self.assertIsNone(app_module.conversation_id_for_chat(UUID, "u"))


class TestMessageChatRoute(unittest.TestCase):
    def test_uuid_message_on_connection_bound_to_other_chat_gets_its_own_session(self):
        chat_id, session_id = app_module.message_chat_route("u", UUID, "-1793565083660")
        self.assertEqual(chat_id, UUID)
        self.assertEqual(session_id, f"ws_u_conv_{UUID}")

    def test_legacy_negative_message_gets_its_own_session(self):
        chat_id, session_id = app_module.message_chat_route("u", "-5", UUID)
        self.assertEqual((chat_id, session_id), ("-5", "ws_u_conv_-5"))

    def test_message_without_chat_id_uses_connection_chat(self):
        chat_id, session_id = app_module.message_chat_route("u", None, UUID)
        self.assertEqual((chat_id, session_id), (UUID, f"ws_u_conv_{UUID}"))


class TestBroadcastKeys(unittest.IsolatedAsyncioTestCase):
    async def test_broadcast_publishes_once_to_client_chat_id_channel(self):
        redis_client = MagicMock()
        redis_client.publish = AsyncMock()
        with patch("app.redis_client", redis_client):
            await app_module.broadcast_to_conversation(UUID, {"type": "broadcast"}, exclude_session="s1")
        redis_client.publish.assert_awaited_once()
        self.assertEqual(redis_client.publish.await_args.args[0], f"conversation:{UUID}")

    @patch("app.client_chat_id_for", return_value=UUID)
    async def test_broadcast_by_int_id_looks_up_client_chat_id(self, mock_cci):
        with patch("app.broadcast_to_conversation", new_callable=AsyncMock) as mock_broadcast:
            await app_module.broadcast_to_conversation_id(19400, {"type": "delete_message"})
        mock_cci.assert_called_once_with(19400)
        mock_broadcast.assert_awaited_once_with(UUID, {"type": "delete_message"}, exclude_session=None)

    @patch("app.client_chat_id_for", return_value=None)
    async def test_broadcast_by_int_id_skips_when_row_gone(self, mock_cci):
        with patch("app.broadcast_to_conversation", new_callable=AsyncMock) as mock_broadcast:
            await app_module.broadcast_to_conversation_id(19400, {"type": "delete_message"})
        mock_broadcast.assert_not_awaited()

    async def test_pending_tools_counter_keyed_by_client_chat_id(self):
        redis_client = MagicMock()
        redis_client.incr = AsyncMock()
        redis_client.expire = AsyncMock()
        with patch("app.redis_client", redis_client):
            await app_module.increment_pending_tools(UUID)
        redis_client.incr.assert_awaited_once_with(f"conversation:{UUID}:pending_tools")


class TestLocalObjectRegistration(unittest.IsolatedAsyncioTestCase):
    async def test_registration_keyed_by_client_chat_id(self):
        local = LocalObjectManager()
        ws = object()
        await local.register_conversation_websocket("ws_u_conv_" + UUID, UUID, ws)
        self.assertEqual(await local.get_conversation_websockets(UUID), [("ws_u_conv_" + UUID, ws)])
        self.assertEqual(await local.get_session_conversations("ws_u_conv_" + UUID), {UUID})
        self.assertIs(await local.get_session_websocket("ws_u_conv_" + UUID), ws)


class TestRedirectMachineryRemoved(unittest.IsolatedAsyncioTestCase):
    async def test_no_session_redirects_or_id_mappings(self):
        # Async test: LocalObjectManager creates asyncio locks, which need a running loop on 3.9
        for name in ("set_redirect", "get_redirect", "resolve_session_id", "rename_session"):
            self.assertFalse(hasattr(ChatSessionManager, name), name)
        for name in ("get_optimistic_id_cached", "get_real_id_cached", "cache_id_mapping", "clear_id_mapping_cache"):
            self.assertFalse(hasattr(LocalObjectManager, name), name)
        self.assertNotIn("session_mappings", create_managers(MagicMock()))
