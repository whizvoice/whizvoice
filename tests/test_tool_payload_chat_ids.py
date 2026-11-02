"""Outbound tool_execution / delete_message payloads carry client_chat_id alongside conversation_id
(and no legacy int id)."""
import contextvars
import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module
import device_control_tools
import maps_tools
import messaging_tools
import music_tools
import screen_agent_tools
import tool_payload_ids

CHAT = "7f3a0c2e-1b4d-4c55-9a77-0d1e2f3a4b5c"


class TestChatIdsForConversation(unittest.TestCase):
    def test_negative_id_has_no_client_chat_id(self):
        self.assertEqual(tool_payload_ids.chat_ids_for_conversation(-77), {"client_chat_id": None})

    def test_positive_id_looks_up_rows_client_chat_id(self):
        with patch("database.client_chat_id_for", return_value=CHAT) as lookup:
            ids = tool_payload_ids.chat_ids_for_conversation(19400)
        lookup.assert_called_once_with(19400)
        self.assertEqual(ids, {"client_chat_id": CHAT})

    def test_none_without_context(self):
        self.assertEqual(tool_payload_ids.chat_ids_for_conversation(None),
                         {"client_chat_id": None})

    def test_running_tools_client_chat_id_wins(self):
        def run():
            tool_payload_ids.current_client_chat_id.set(CHAT)
            with patch("database.client_chat_id_for") as lookup:
                ids = tool_payload_ids.tool_payload_chat_ids(19400)
            lookup.assert_not_called()
            return ids
        ids = contextvars.copy_context().run(run)
        self.assertEqual(ids["client_chat_id"], CHAT)
        self.assertIsNone(tool_payload_ids.current_client_chat_id.get())  # did not leak


class TestToolModulesPayloads(unittest.IsolatedAsyncioTestCase):
    """One tool per module: the sent tool_execution frame carries the chat ids."""

    async def sent_frame(self, call, conversation_id):
        ws = MagicMock()
        ws.send_text = AsyncMock()
        await call(ws, conversation_id)
        frame = json.loads(ws.send_text.await_args.args[0])
        self.assertEqual(frame["type"], "tool_execution")
        return frame

    async def assert_carries_ids(self, call):
        frame = await self.sent_frame(call, -77)
        self.assertEqual(frame["conversation_id"], -77)
        self.assertIsNone(frame["client_chat_id"])
        self.assertNotIn("client_conversation_id", frame)

        with patch("database.client_chat_id_for", return_value=CHAT):
            frame = await self.sent_frame(call, 19400)
        self.assertEqual(frame["conversation_id"], 19400)
        self.assertEqual(frame["client_chat_id"], CHAT)
        self.assertNotIn("client_conversation_id", frame)

    async def test_device_control_tools(self):
        await self.assert_carries_ids(lambda ws, cid: device_control_tools._send_device_tool(
            "agent_flashlight", {}, user_id="u1", websocket=ws, conversation_id=cid))

    async def test_maps_tools(self):
        await self.assert_carries_ids(lambda ws, cid: maps_tools.agent_search_google_maps_location(
            "1 Main St", user_id="u1", websocket=ws, conversation_id=cid))

    async def test_music_tools(self):
        await self.assert_carries_ids(lambda ws, cid: music_tools.agent_play_youtube_music(
            "song", user_id="u1", websocket=ws, conversation_id=cid))

    async def test_messaging_tools(self):
        await self.assert_carries_ids(lambda ws, cid: messaging_tools.agent_whatsapp_select_chat(
            "Bob", user_id="u1", websocket=ws, conversation_id=cid))

    async def test_screen_agent_tools(self):
        await self.assert_carries_ids(lambda ws, cid: screen_agent_tools._send_tool_and_wait(
            "agent_open_health_app_settings", {}, "u1", ws, None, cid))


class TestDeleteMessagePayload(unittest.TestCase):
    def test_carries_client_chat_id_of_conversation(self):
        with patch("app.client_chat_id_for", return_value="-77") as lookup:
            payload = app_module.delete_message_payload(5, 19400, "req-1", "superseded_by_new_request")
        lookup.assert_called_once_with(19400)
        self.assertEqual(payload, {
            "type": "delete_message", "message_id": 5, "conversation_id": 19400,
            "client_chat_id": "-77",
            "request_id": "req-1", "reason": "superseded_by_new_request",
        })

    def test_uuid_chat_has_no_legacy_id(self):
        with patch("app.client_chat_id_for", return_value=CHAT):
            payload = app_module.delete_message_payload(None, 19400, "req-1", "x")
        self.assertEqual(payload["client_chat_id"], CHAT)
        self.assertNotIn("client_conversation_id", payload)


class TestCancelAndBroadcastUsesPayload(unittest.IsolatedAsyncioTestCase):
    async def test_broadcast_payload_has_client_chat_id(self):
        with patch("app.supabase"), \
             patch("app.client_chat_id_for", return_value=CHAT), \
             patch("app.broadcast_to_conversation_id", new=AsyncMock()) as broadcast:
            await app_module.cancel_and_broadcast_messages([5], 19400, "req-1", "superseded_by_new_message")
        payload = broadcast.await_args.args[1]
        self.assertEqual(payload["type"], "delete_message")
        self.assertEqual(payload["client_chat_id"], CHAT)


if __name__ == "__main__":
    unittest.main()
