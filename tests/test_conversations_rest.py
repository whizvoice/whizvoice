import unittest
import sys
import os
from unittest.mock import patch, MagicMock

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
from postgrest import SyncPostgrestClient

import app as app_module

UUID_REF = "7f3a0c2e-1b4d-4c55-9a77-0d1e2f3a4b5c"


def _row(conv_id=19400, client_chat_id=UUID_REF):
    return {
        "id": conv_id,
        "user_id": "user-1",
        "title": "Hello",
        "created_at": "2026-11-01T00:00:00+00:00",
        "last_message_time": "2026-11-01T00:00:00+00:00",
        "updated_at": "2026-11-01T00:00:00+00:00",
        "source": "app",
        "google_session_id": None,
        "deleted_at": None,
        "client_chat_id": client_chat_id,
    }


# Starlette's TestClient is incompatible with the pinned httpx 0.28 (it passes
# the removed `app=` kwarg), so drive the app through httpx's ASGITransport.
class TestConversationsRest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        app_module.app.dependency_overrides[app_module.get_current_user] = (
            lambda: {"sub": "user-1"}
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_module.app), base_url="http://test"
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        app_module.app.dependency_overrides.clear()

    @patch("app.resolve_conversation_id", return_value=19400)
    @patch("app.supabase")
    async def test_get_conversation_by_uuid_path(self, mock_supabase, mock_resolve):
        result = MagicMock()
        result.data = [_row()]
        (mock_supabase.table.return_value.select.return_value
            .eq.return_value.eq.return_value.is_.return_value
            .execute.return_value) = result

        resp = await self.client.get(f"/conversations/{UUID_REF}")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["id"], 19400)
        self.assertEqual(resp.json()["client_chat_id"], UUID_REF)
        mock_resolve.assert_called_once_with(UUID_REF, "user-1")

    @patch("app.resolve_conversation_id", return_value=None)
    @patch("app.supabase")
    async def test_get_conversation_unowned_uuid_404(self, mock_supabase, mock_resolve):
        resp = await self.client.get(f"/conversations/{UUID_REF}")
        self.assertEqual(resp.status_code, 404)

    @patch("app.supabase")
    async def test_get_conversations_pages_past_1000(self, mock_supabase):
        page1 = MagicMock()
        page1.data = [_row(i) for i in range(1000)]
        page2 = MagicMock()
        page2.data = [_row(1000 + i) for i in range(3)]
        query = (mock_supabase.table.return_value.select.return_value
                 .eq.return_value.order.return_value.order.return_value
                 .is_.return_value)
        query.range.return_value.execute.side_effect = [page1, page2]

        resp = await self.client.get("/conversations")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["count"], 1003)
        self.assertEqual(query.range.call_count, 2)
        query.range.assert_any_call(0, 999)
        query.range.assert_any_call(1000, 1999)

    @patch("app.supabase")
    async def test_get_conversations_exactly_1000_rows_stops_on_empty_page(self, mock_supabase):
        page1 = MagicMock()
        page1.data = [_row(i) for i in range(1000)]
        page2 = MagicMock()
        page2.data = []
        query = (mock_supabase.table.return_value.select.return_value
                 .eq.return_value.order.return_value.order.return_value
                 .is_.return_value)
        query.range.return_value.execute.side_effect = [page1, page2]

        resp = await self.client.get("/conversations")

        self.assertEqual(resp.json()["count"], 1000)
        self.assertEqual(query.range.call_count, 2)

    async def test_get_conversations_real_builder_sends_single_offset_and_limit(self):
        """A real postgrest builder mutates params in .range(); reusing one builder
        across pages would send duplicate offset/limit params."""
        urls = []

        def handler(request):
            urls.append(request.url)
            n = 1000 if len(urls) == 1 else 3
            return httpx.Response(200, json=[_row(i) for i in range(n)])

        real = SyncPostgrestClient("http://db.test/rest/v1", headers={})
        real.session = httpx.Client(
            base_url="http://db.test/rest/v1", transport=httpx.MockTransport(handler)
        )
        with patch("app.supabase", real):
            resp = await self.client.get("/conversations")

        self.assertEqual(resp.json()["count"], 1003)
        self.assertEqual(len(urls), 2)
        self.assertEqual(urls[0].params.get_list("offset"), ["0"])
        self.assertEqual(urls[0].params.get_list("limit"), ["1000"])
        self.assertEqual(urls[1].params.get_list("offset"), ["1000"])
        self.assertEqual(urls[1].params.get_list("limit"), ["1000"])
        self.assertEqual(urls[1].params.get_list("order"), ["updated_at.desc,id.desc"])

    @patch("app.resolve_conversation_id", return_value=None)
    async def test_pending_requests_unresolvable_ref_is_soft(self, mock_resolve):
        resp = await self.client.get(f"/api/conversations/{UUID_REF}/pending-requests")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"has_pending": False, "request_ids": []})

    @patch("app.get_request_state")
    @patch("app.get_active_requests")
    @patch("app.client_chat_id_for", return_value=UUID_REF)
    @patch("app.resolve_conversation_id", return_value=19400)
    async def test_pending_requests_reads_session_keyed_by_client_chat_id(
        self, mock_resolve, mock_cci, mock_active, mock_state
    ):
        async def active(session_id):
            return {"req-1"} if session_id == f"ws_user-1_conv_{UUID_REF}" else set()

        async def state(request_id):
            return None

        mock_active.side_effect = active
        mock_state.side_effect = state

        resp = await self.client.get("/api/conversations/19400/pending-requests")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"has_pending": True, "request_ids": ["req-1"]})
        mock_cci.assert_called_once_with(19400)
        mock_active.assert_called_once_with(f"ws_user-1_conv_{UUID_REF}")

    @patch("app.client_chat_id_for", return_value=UUID_REF)
    @patch("app.invalidate_conversation_cache")
    @patch("app.resolve_conversation_id", return_value=19400)
    @patch("app.supabase")
    async def test_delete_conversation_returns_int_id_and_client_chat_id(
        self, mock_supabase, mock_resolve, mock_invalidate, mock_cc
    ):
        verify = MagicMock()
        verify.data = [{"deleted_at": None}]
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = verify
        upd = MagicMock()
        upd.data = [_row()]
        (mock_supabase.table.return_value.update.return_value
            .eq.return_value.eq.return_value.execute.return_value) = upd

        resp = await self.client.delete(f"/conversations/{UUID_REF}")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["conversation_id"], 19400)
        self.assertEqual(resp.json()["client_chat_id"], UUID_REF)
        mock_invalidate.assert_called_once_with(19400)

    @patch("app.supabase")
    async def test_create_conversation_stores_client_chat_id(self, mock_supabase):
        result = MagicMock()
        result.data = [_row()]
        mock_supabase.table.return_value.insert.return_value.execute.return_value = result

        resp = await self.client.post(
            "/conversations", json={"title": "Hello", "client_chat_id": UUID_REF}
        )

        self.assertEqual(resp.status_code, 200)
        inserted = mock_supabase.table.return_value.insert.call_args[0][0]
        self.assertEqual(inserted["client_chat_id"], UUID_REF)


if __name__ == "__main__":
    unittest.main()
