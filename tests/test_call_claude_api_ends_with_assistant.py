import unittest
import sys
import os
from unittest.mock import patch, MagicMock, AsyncMock

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app

# Must match the filler inserted by the guard in app.call_claude_api.
FILLER = "[system: previous tool result returned above; no new message from the user]"


class TestCallClaudeApiEndsWithAssistant(unittest.IsolatedAsyncioTestCase):
    """Guard: the message list sent to Claude must never end on an assistant message.

    Under concurrent requests on one conversation, a sibling request can finish and append its
    final assistant turn to the shared per-conversation context while this request is still mid
    tool-loop. When this request re-reads the context to continue, the list ends on that sibling's
    assistant message and the Claude API rejects it ("must end with a user message"). The guard
    appends a persisted hidden_text user filler so the call stays valid.
    """

    def setUp(self):
        self.user_id = "113263970931737678872"
        self.real_id = 19400
        self.session_id = f"ws_{self.user_id}_conv_{self.real_id}"

    @patch("app.get_current_datetime", return_value="2026-06-30 12:00:00")
    @patch("app.add_chat_message", new_callable=AsyncMock)
    @patch("app.save_message_to_db")
    @patch("app.get_chat_messages_for_claude", new_callable=AsyncMock)
    async def test_ends_with_assistant_inserts_persisted_filler(
        self, mock_get_messages, mock_save, mock_add_chat, mock_dt,
    ):
        mock_client = MagicMock()
        # Context as a lapped request would see it: a sibling's final assistant text at the tail.
        mock_get_messages.return_value = [
            {"role": "user", "content": "send the message about Fox"},
            {"role": "assistant", "content": "Does that look right?"},
        ]
        mock_save.return_value = (self.real_id, 999, [], "2026-06-30T12:00:00Z")

        await app.call_claude_api(
            mock_client,
            session_id=self.session_id,
            stream=False,
            conversation_id=self.real_id,
            with_tools=False,
            user_id=self.user_id,
        )

        # 1. Outgoing message list ends on the user filler, not the assistant message.
        sent = mock_client.messages.create.call_args.kwargs["messages"]
        self.assertEqual(sent[-1]["role"], "user")
        self.assertEqual(sent[-1]["content"], FILLER)

        # 2. Persisted to the DB as hidden_text (permanent history, not a temporary patch).
        mock_save.assert_called_once()
        save_args, save_kwargs = mock_save.call_args
        self.assertEqual(save_kwargs.get("content_type"), "hidden_text")
        self.assertEqual(save_args[0], self.user_id)
        self.assertEqual(save_args[1], self.real_id)
        self.assertEqual(save_args[2], FILLER)
        self.assertEqual(save_args[3], "USER")
        # request_id intentionally omitted so the filler isn't pulled into another request's
        # timestamp window (which would re-create the ends-on-assistant state).
        self.assertIsNone(save_kwargs.get("request_id"))

        # 3. Mirrored into the live Redis context so DB and Redis agree.
        mock_add_chat.assert_awaited_once()
        add_args, _ = mock_add_chat.call_args
        self.assertEqual(add_args[0], self.session_id)
        self.assertEqual(add_args[1], {"role": "user", "content": FILLER})

    @patch("app.get_current_datetime", return_value="2026-06-30 12:00:00")
    @patch("app.add_chat_message", new_callable=AsyncMock)
    @patch("app.save_message_to_db")
    @patch("app.get_chat_messages_for_claude", new_callable=AsyncMock)
    async def test_ends_with_user_no_filler(
        self, mock_get_messages, mock_save, mock_add_chat, mock_dt,
    ):
        mock_client = MagicMock()
        # Normal case: context already ends on a user turn.
        mock_get_messages.return_value = [
            {"role": "assistant", "content": "What would you like to say?"},
            {"role": "user", "content": "send the message about Fox"},
        ]

        await app.call_claude_api(
            mock_client,
            session_id=self.session_id,
            stream=False,
            conversation_id=self.real_id,
            with_tools=False,
            user_id=self.user_id,
        )

        sent = mock_client.messages.create.call_args.kwargs["messages"]
        self.assertEqual(sent[-1]["role"], "user")
        self.assertEqual(sent[-1]["content"], "send the message about Fox")
        # No filler => nothing persisted.
        mock_save.assert_not_called()
        mock_add_chat.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
