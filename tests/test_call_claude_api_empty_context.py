import unittest
import sys
import os
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app


class TestCallClaudeApiEmptyContext(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.user_id = "113263970931737678872"
        self.optimistic_id = -1776298266784
        self.real_id = 19400
        self.session_id = f"ws_{self.user_id}_conv_{self.real_id}"

    @patch("app.get_parent_task_preference", new_callable=AsyncMock)
    @patch("app.set_chat_messages", new_callable=AsyncMock)
    @patch("app.get_chat_messages_for_claude", new_callable=AsyncMock)
    @patch("app.resolve_conversation_id")
    @patch("app.supabase")
    async def test_empty_redis_with_optimistic_id_uses_resolved_real_id(
        self, mock_supabase, mock_resolve, mock_get_messages, mock_set_messages,
        mock_parent_pref,
    ):
        """Empty Redis + optimistic conversation_id: resolver converts to real ID, messages query uses real ID."""
        mock_client = MagicMock()
        mock_get_messages.side_effect = [[], [{"role": "user", "content": "hi"}]]
        mock_resolve.return_value = self.real_id
        mock_parent_pref.return_value = "false"

        captured_conv_id = {}
        def fake_eq(col, val):
            if col == "conversation_id":
                captured_conv_id["value"] = val
            return eq_chain
        eq_chain = MagicMock()
        eq_chain.eq = fake_eq
        eq_chain.order.return_value.execute.return_value.data = [{
            "id": 1, "content": "hi", "message_sender": "USER",
            "timestamp": "2026-04-16T00:11:08Z", "cancelled": None,
            "content_type": "text", "tool_content": None, "request_id": "r1",
        }]
        mock_supabase.table.return_value.select.return_value.eq = fake_eq

        await app.call_claude_api(
            mock_client,
            session_id=self.session_id,
            stream=False,
            conversation_id=self.optimistic_id,
            with_tools=False,
            user_id=self.user_id,
        )

        mock_resolve.assert_called_once_with(self.optimistic_id, self.user_id)
        self.assertEqual(captured_conv_id["value"], self.real_id,
                         "Messages query should use RESOLVED real ID, not the optimistic one")

    @patch("app.get_parent_task_preference", new_callable=AsyncMock)
    @patch("app.set_chat_messages", new_callable=AsyncMock)
    @patch("app.get_chat_messages_for_claude", new_callable=AsyncMock)
    @patch("app.resolve_conversation_id")
    @patch("app.supabase")
    async def test_resolver_returns_none_skips_db_reload(
        self, mock_supabase, mock_resolve, mock_get_messages, mock_set_messages,
        mock_parent_pref,
    ):
        """If resolver returns None (row not found yet), DB reload is skipped, not called with None."""
        mock_client = MagicMock()
        mock_get_messages.return_value = []
        mock_resolve.return_value = None
        mock_parent_pref.return_value = "false"

        await app.call_claude_api(
            mock_client,
            session_id=self.session_id,
            stream=False,
            conversation_id=self.optimistic_id,
            with_tools=False,
            user_id=self.user_id,
        )

        mock_resolve.assert_called_once_with(self.optimistic_id, self.user_id)
        mock_supabase.table.assert_not_called()


if __name__ == "__main__":
    unittest.main()
