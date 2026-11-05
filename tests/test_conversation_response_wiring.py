import unittest
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pydantic import ValidationError

from models import ConversationResponse


ROW = {
    "id": 1,
    "user_id": "user-1",
    "title": "Chat",
    "created_at": "2026-11-01T00:00:00Z",
    "last_message_time": "2026-11-01T00:00:00Z",
    "source": "app",
    "google_session_id": None,
    "deleted_at": None,
    "client_chat_id": "6f1c2e0a-3b4d-4e5f-8a9b-0c1d2e3f4a5b",
}


class TestConversationResponseWiring(unittest.TestCase):
    def test_row_with_client_chat_id_builds(self):
        resp = ConversationResponse(
            id=ROW["id"],
            user_id=ROW["user_id"],
            title=ROW["title"],
            created_at=ROW["created_at"],
            last_message_time=ROW["last_message_time"],
            source=ROW["source"],
            google_session_id=ROW.get("google_session_id"),
            deleted_at=ROW.get("deleted_at"),
            client_chat_id=ROW["client_chat_id"],
        )
        self.assertEqual(resp.client_chat_id, ROW["client_chat_id"])

    def test_omitting_client_chat_id_raises(self):
        row = {k: v for k, v in ROW.items() if k != "client_chat_id"}
        with self.assertRaises(ValidationError):
            ConversationResponse(**row)


if __name__ == "__main__":
    unittest.main()
