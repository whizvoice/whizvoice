import unittest
import sys
import os
from unittest.mock import patch, MagicMock

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from postgrest.exceptions import APIError

import database
from database import (
    invalidate_conversation_cache,
    resolve_conversation_id,
    client_chat_id_for,
    clear_id_cache,
    save_message_to_db,
)


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
            ("client_chat_id", str(self.optimistic_id)),
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

    @patch("database.supabase")
    def test_uuid_string_resolves_via_client_chat_id(self, mock_supabase):
        """A UUID string is looked up by client_chat_id."""
        uuid = "7f3a1c52-9d4e-4b8a-a1c3-2e5f6a7b8c9d"
        mock_execute = MagicMock()
        mock_execute.data = [{"id": self.real_id}]
        (mock_supabase.table.return_value.select.return_value
            .eq.return_value.eq.return_value.is_.return_value
            .execute.return_value) = mock_execute

        result = resolve_conversation_id(uuid, self.user_id)

        self.assertEqual(result, self.real_id)
        eq_calls = mock_supabase.table.return_value.select.return_value.eq.call_args_list
        self.assertIn(("client_chat_id", uuid), [(c.args[0], c.args[1]) for c in eq_calls])

    @patch("database.supabase")
    def test_numeric_string_positive_verifies_ownership(self, mock_supabase):
        """A positive numeric string behaves like the int: ownership check on id."""
        mock_execute = MagicMock()
        mock_execute.data = [{"id": self.real_id}]
        (mock_supabase.table.return_value.select.return_value
            .eq.return_value.eq.return_value.is_.return_value
            .execute.return_value) = mock_execute

        result = resolve_conversation_id("19400", self.user_id)

        self.assertEqual(result, self.real_id)
        eq_calls = mock_supabase.table.return_value.select.return_value.eq.call_args_list
        self.assertIn(("id", self.real_id), [(c.args[0], c.args[1]) for c in eq_calls])

    @patch("database.supabase")
    def test_negative_int_maps_to_string_form(self, mock_supabase):
        """A legacy negative int is looked up as its str() in client_chat_id."""
        mock_execute = MagicMock()
        mock_execute.data = [{"id": self.real_id}]
        (mock_supabase.table.return_value.select.return_value
            .eq.return_value.eq.return_value.is_.return_value
            .execute.return_value) = mock_execute

        result = resolve_conversation_id(-1776298266784, self.user_id)

        self.assertEqual(result, self.real_id)
        eq_calls = mock_supabase.table.return_value.select.return_value.eq.call_args_list
        self.assertIn(("client_chat_id", "-1776298266784"), [(c.args[0], c.args[1]) for c in eq_calls])

    @patch("database.supabase")
    def test_unknown_uuid_returns_none(self, mock_supabase):
        mock_execute = MagicMock()
        mock_execute.data = []
        (mock_supabase.table.return_value.select.return_value
            .eq.return_value.eq.return_value.is_.return_value
            .execute.return_value) = mock_execute

        self.assertIsNone(resolve_conversation_id("00000000-0000-0000-0000-000000000000", self.user_id))

    @patch("database.supabase")
    def test_client_chat_id_for_caches_second_call(self, mock_supabase):
        clear_id_cache()
        mock_execute = MagicMock()
        mock_execute.data = [{"client_chat_id": "abc-uuid"}]
        (mock_supabase.table.return_value.select.return_value
            .eq.return_value.execute.return_value) = mock_execute

        first = client_chat_id_for(self.real_id)
        second = client_chat_id_for(self.real_id)

        self.assertEqual(first, "abc-uuid")
        self.assertEqual(second, "abc-uuid")
        self.assertEqual(mock_supabase.table.call_count, 1)
        clear_id_cache()

    @patch("database.supabase")
    def test_save_message_unique_violation_rereads_row(self, mock_supabase):
        """Concurrent create: insert raises 23505, so re-read by client_chat_id."""
        conversations = MagicMock()
        lookup = conversations.select.return_value.eq.return_value.eq.return_value.is_.return_value
        lookup.execute.side_effect = [MagicMock(data=[]), MagicMock(data=[{"id": self.real_id}])]
        conversations.insert.return_value.execute.side_effect = APIError(
            {"message": "duplicate key", "code": "23505", "details": "", "hint": ""}
        )
        messages = MagicMock()
        messages.insert.return_value.execute.return_value = MagicMock(
            data=[{"id": 77, "timestamp": "2026-11-01T00:00:00Z", "conversation_id": self.real_id}]
        )
        mock_supabase.table.side_effect = lambda name: conversations if name == "conversations" else messages

        result = save_message_to_db(
            self.user_id, None, "hello", "USER",
            client_chat_id="7f3a1c52-9d4e-4b8a-a1c3-2e5f6a7b8c9d",
            client_timestamp="2026-11-01T00:00:00Z",
        )

        self.assertEqual(result[0], self.real_id)
        self.assertEqual(result[1], 77)

    @patch("database.supabase")
    def test_stale_cache_entry_after_soft_delete_creates_new_conversation(self, mock_supabase):
        """Cached id whose row is now soft-deleted: drop entry and create a new conversation."""
        clear_id_cache()
        cid = "7f3a1c52-9d4e-4b8a-a1c3-2e5f6a7b8c9d"
        database._conversation_id_by_client_chat_id[(self.user_id, cid)] = self.real_id
        database._client_chat_id_by_conversation_id[self.real_id] = cid

        conversations = MagicMock()
        # exists-and-not-deleted check returns nothing (row soft-deleted)
        (conversations.select.return_value.eq.return_value.eq.return_value
            .is_.return_value.execute.return_value) = MagicMock(data=[])
        conversations.insert.return_value.execute.return_value = MagicMock(
            data=[{"id": 99, "created_at": "t", "updated_at": "t"}]
        )
        messages = MagicMock()
        messages.insert.return_value.execute.return_value = MagicMock(
            data=[{"id": 78, "timestamp": "2026-11-01T00:00:00Z", "conversation_id": 99}]
        )
        mock_supabase.table.side_effect = lambda name: conversations if name == "conversations" else messages

        result = save_message_to_db(
            self.user_id, None, "hello", "USER",
            client_chat_id=cid, client_timestamp="2026-11-01T00:00:00Z",
        )

        self.assertEqual(result[0], 99)
        conversations.insert.assert_called_once()
        self.assertNotIn((self.user_id, cid), database._conversation_id_by_client_chat_id)
        self.assertNotIn(self.real_id, database._client_chat_id_by_conversation_id)
        clear_id_cache()

    def test_invalidate_conversation_cache_drops_both_entries(self):
        clear_id_cache()
        database._conversation_id_by_client_chat_id[(self.user_id, "x")] = 5
        database._client_chat_id_by_conversation_id[5] = "x"
        invalidate_conversation_cache(5)
        self.assertEqual(database._conversation_id_by_client_chat_id, {})
        self.assertEqual(database._client_chat_id_by_conversation_id, {})

    @patch("database.supabase")
    def test_client_conversation_id_alias_matches_client_chat_id(self, mock_supabase):
        """Deprecated client_conversation_id=-5 behaves exactly like client_chat_id="-5"."""
        def run(**kwargs):
            clear_id_cache()
            conversations = MagicMock()
            lookup = conversations.select.return_value.eq.return_value.eq.return_value.is_.return_value
            lookup.execute.return_value = MagicMock(data=[])
            conversations.insert.return_value.execute.return_value = MagicMock(
                data=[{"id": 99, "created_at": "t", "updated_at": "t"}]
            )
            messages = MagicMock()
            messages.insert.return_value.execute.return_value = MagicMock(
                data=[{"id": 78, "timestamp": "2026-11-01T00:00:00Z", "conversation_id": 99}]
            )
            mock_supabase.table.side_effect = lambda name: conversations if name == "conversations" else messages
            result = save_message_to_db(
                self.user_id, None, "hello", "USER",
                client_timestamp="2026-11-01T00:00:00Z", **kwargs
            )
            return result, conversations.insert.call_args

        alias_result, alias_insert = run(client_conversation_id=-5)
        new_result, new_insert = run(client_chat_id="-5")

        self.assertEqual(alias_result, new_result)
        self.assertEqual(alias_insert, new_insert)
        self.assertEqual(alias_insert.args[0]["client_chat_id"], "-5")


if __name__ == "__main__":
    unittest.main()
