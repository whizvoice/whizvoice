import unittest
import sys
import os
from unittest.mock import patch, MagicMock

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import resolve_conversation_id


class TestResolveConversationId(unittest.TestCase):
    def setUp(self):
        self.user_id = "113263970931737678872"
        self.optimistic_id = -1776298266784
        self.real_id = 19400

    @patch("database.supabase")
    def test_negative_id_resolves_to_real_id(self, mock_supabase):
        """Negative optimistic_id returns the real ID from conversations.optimistic_chat_id.

        The column stores optimistic IDs as strings (see database.py and app.py call
        sites). Soft-deleted rows are excluded via is_('deleted_at', 'null').
        """
        mock_execute = MagicMock()
        mock_execute.data = [{"id": self.real_id}]
        (mock_supabase.table.return_value.select.return_value
            .eq.return_value.eq.return_value.is_.return_value
            .execute.return_value) = mock_execute

        result = resolve_conversation_id(self.optimistic_id, self.user_id)

        self.assertEqual(result, self.real_id)
        mock_supabase.table.assert_called_once_with("conversations")
        # Verify the optimistic_chat_id was stringified for the .eq() call
        eq_calls = mock_supabase.table.return_value.select.return_value.eq.call_args_list
        self.assertIn(
            ("optimistic_chat_id", str(self.optimistic_id)),
            [(c.args[0], c.args[1]) for c in eq_calls],
        )

    @patch("database.supabase")
    def test_negative_id_not_found_returns_none(self, mock_supabase):
        """Negative optimistic_id with no matching row returns None."""
        mock_execute = MagicMock()
        mock_execute.data = []
        (mock_supabase.table.return_value.select.return_value
            .eq.return_value.eq.return_value.is_.return_value
            .execute.return_value) = mock_execute

        result = resolve_conversation_id(self.optimistic_id, self.user_id)

        self.assertIsNone(result)

    @patch("database.supabase")
    def test_positive_id_owned_returns_same_id(self, mock_supabase):
        """Positive ID that the user owns is returned unchanged."""
        mock_execute = MagicMock()
        mock_execute.data = [{"id": self.real_id}]
        (mock_supabase.table.return_value.select.return_value
            .eq.return_value.eq.return_value.is_.return_value
            .execute.return_value) = mock_execute

        result = resolve_conversation_id(self.real_id, self.user_id)

        self.assertEqual(result, self.real_id)
        eq_calls = mock_supabase.table.return_value.select.return_value.eq.call_args_list
        self.assertIn(
            ("id", self.real_id),
            [(c.args[0], c.args[1]) for c in eq_calls],
        )

    @patch("database.supabase")
    def test_positive_id_not_owned_returns_none(self, mock_supabase):
        """Positive ID not owned by user (or soft-deleted) returns None."""
        mock_execute = MagicMock()
        mock_execute.data = []
        (mock_supabase.table.return_value.select.return_value
            .eq.return_value.eq.return_value.is_.return_value
            .execute.return_value) = mock_execute

        result = resolve_conversation_id(self.real_id, self.user_id)

        self.assertIsNone(result)

    @patch("database.supabase")
    def test_none_returns_none(self, mock_supabase):
        """None input returns None without DB call."""
        result = resolve_conversation_id(None, self.user_id)

        self.assertIsNone(result)
        mock_supabase.table.assert_not_called()

    @patch("database.supabase")
    def test_supabase_exception_returns_none(self, mock_supabase):
        """DB errors are logged and None is returned (caller falls through)."""
        mock_supabase.table.side_effect = RuntimeError("supabase unavailable")

        result = resolve_conversation_id(self.optimistic_id, self.user_id)

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
