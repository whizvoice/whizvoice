"""Endpoint-level tests: websocket_endpoint keys sessions, channels and sockets by client_chat_id."""
import asyncio
import json
import os
import sys
import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import WebSocketDisconnect

import app as app_module
from redis_managers import LocalObjectManager

USER = "u1"
CHAT_A = "7f3a0c2e-1b4d-4c55-9a77-0d1e2f3a4b5c"
CHAT_B = "0b9e8d7c-6a5f-4e3d-2c1b-0a9f8e7d6c5b"


class FakeWebSocket:
    """Minimal WebSocket: replays queued inbound messages, then disconnects."""

    def __init__(self, query_params=None, inbound=()):
        self.headers = {"authorization": "Bearer token"}
        self.query_params = dict(query_params or {})
        self._inbound = list(inbound)
        self.accept = AsyncMock()
        self.send_text = AsyncMock()
        self.send_json = AsyncMock()
        self.close = AsyncMock()

    async def receive_text(self):
        if self._inbound:
            return self._inbound.pop(0)
        raise WebSocketDisconnect()


def user_message(client_chat_id=None, request_id="req-1"):
    data = {"type": "message", "message": "hi", "request_id": request_id,
            "timestamp": "2026-11-01T00:00:00.000Z"}
    if client_chat_id is not None:
        data["client_chat_id"] = client_chat_id
    return json.dumps(data)


class EndpointHarness(unittest.IsolatedAsyncioTestCase):
    """Patches everything around websocket_endpoint except its keying logic."""

    async def asyncSetUp(self):
        self.stack = ExitStack()
        p = lambda target, **kw: self.stack.enter_context(patch(target, **kw))
        p("jose.jwt.decode", return_value={"sub": USER, "email": "u1@example.com"})
        p("app.get_total_session_count", new=AsyncMock(return_value=0))
        p("app.update_session_activity_redis", new=AsyncMock())
        p("app.load_conversation_history", return_value=[])
        self.set_chat_messages = p("app.set_chat_messages", new=AsyncMock())
        p("app.add_user_session", new=AsyncMock())
        p("app.evict_user_sessions_if_needed", new=AsyncMock())
        self.register = p("app.register_websocket_for_conversation", new=AsyncMock())
        self.subscribe = p("app.subscribe_to_conversation", new=AsyncMock())
        self.process = p("app.process_message_task", new=AsyncMock(return_value=None))
        self.cleanup = p("app.cleanup_session", new=AsyncMock())
        p("app.get_active_requests", new=AsyncMock(return_value=set()))
        local = MagicMock()
        local.add_task = AsyncMock()
        local.remove_task = AsyncMock()
        local.get_and_cancel_task = AsyncMock(return_value=None)
        local.unregister_conversation_websocket = AsyncMock()
        active = MagicMock()
        active.add = AsyncMock()
        active.remove = AsyncMock()
        p("app.redis_managers", new={"local_objects": local, "active_requests": active})
        self.resolve = p("app.resolve_conversation_id", return_value=None)
        self.cci_for = p("app.client_chat_id_for", return_value=None)
        self.conv_id_for = p("app.conversation_id_for", return_value=None)

    async def asyncTearDown(self):
        self.stack.close()

    async def run_endpoint(self, ws):
        await app_module.websocket_endpoint(ws)
        await asyncio.sleep(0)  # let the spawned processing/completion tasks finish

    def origin_of_first_registration(self):
        return self.register.await_args_list[0].args[0]


class TestConnectKeys(EndpointHarness):
    async def test_client_chat_id_param_registers_and_subscribes_under_that_chat(self):
        ws = FakeWebSocket({"client_chat_id": CHAT_A})
        await self.run_endpoint(ws)

        origin, chat_id, sock = self.register.await_args.args
        self.assertEqual((chat_id, sock), (CHAT_A, ws))
        self.assertTrue(origin.startswith(f"ws_{USER}_conn_"))
        self.subscribe.assert_awaited_once_with(origin, CHAT_A, ws)
        self.set_chat_messages.assert_awaited_once_with(f"ws_{USER}_conv_{CHAT_A}", [])
        self.cleanup.assert_awaited_once()
        self.assertEqual(self.cleanup.await_args.args[0], origin)

    async def test_conversation_id_param_lands_on_rows_client_chat_id(self):
        self.resolve.return_value = 19400
        self.cci_for.return_value = CHAT_A
        ws = FakeWebSocket({"conversation_id": "19400"})
        await self.run_endpoint(ws)

        self.cci_for.assert_called_with(19400)
        origin, chat_id, _ = self.register.await_args.args
        self.assertEqual(chat_id, CHAT_A)
        self.subscribe.assert_awaited_once_with(origin, CHAT_A, ws)
        self.set_chat_messages.assert_awaited_once_with(f"ws_{USER}_conv_{CHAT_A}", [])

    async def test_each_socket_gets_its_own_origin_session(self):
        await self.run_endpoint(FakeWebSocket({"client_chat_id": CHAT_A}))
        await self.run_endpoint(FakeWebSocket({"client_chat_id": CHAT_A}))
        origins = [c.args[0] for c in self.register.await_args_list]
        self.assertEqual(len(set(origins)), 2)


class TestNoRefBinding(EndpointHarness):
    async def test_binds_to_chat_named_by_first_message(self):
        ws = FakeWebSocket(inbound=[user_message(CHAT_A)])
        await self.run_endpoint(ws)

        self.register.assert_awaited_once()
        origin, chat_id, _ = self.register.await_args.args
        self.assertEqual(chat_id, CHAT_A)
        self.subscribe.assert_awaited_once_with(origin, CHAT_A, ws)
        kwargs = self.process.await_args.kwargs
        self.assertEqual(kwargs["session_id"], f"ws_{USER}_conv_{CHAT_A}")
        self.assertEqual(kwargs["client_chat_id"], CHAT_A)
        self.assertIsNone(kwargs["session_conversation_id"])
        self.assertEqual(kwargs["origin_session_id"], origin)

    async def test_negative_number_string_is_an_ordinary_chat_id(self):
        # Rows migrated from the old app hold their negative number as client_chat_id text;
        # the new app sends it back as a plain string and nothing special-cases it.
        ws = FakeWebSocket(inbound=[user_message("-5")])
        await self.run_endpoint(ws)

        origin, chat_id, _ = self.register.await_args.args
        self.assertEqual(chat_id, "-5")
        self.subscribe.assert_awaited_once_with(origin, "-5", ws)
        kwargs = self.process.await_args.kwargs
        self.assertEqual(kwargs["session_id"], f"ws_{USER}_conv_-5")
        self.assertEqual(kwargs["client_chat_id"], "-5")
        self.assertIsNone(kwargs["session_conversation_id"])

    async def test_binds_to_minted_chat_when_first_message_names_none(self):
        ws = FakeWebSocket(inbound=[user_message(None)])
        await self.run_endpoint(ws)

        self.register.assert_awaited_once()
        origin, chat_id, _ = self.register.await_args.args
        self.subscribe.assert_awaited_once_with(origin, chat_id, ws)
        kwargs = self.process.await_args.kwargs
        # The message is saved under the same minted chat the socket is subscribed to
        self.assertEqual(kwargs["client_chat_id"], chat_id)
        self.assertEqual(kwargs["session_id"], f"ws_{USER}_conv_{chat_id}")
        self.assertIsNone(kwargs["session_conversation_id"])
        self.assertEqual(kwargs["origin_session_id"], origin)

    async def test_no_registration_before_first_message(self):
        await self.run_endpoint(FakeWebSocket())
        self.register.assert_not_awaited()
        self.subscribe.assert_not_awaited()

    async def test_non_message_first_frame_without_chat_id_does_not_bind(self):
        cancel = json.dumps({"type": "cancel", "cancel_request_id": "req-0",
                             "timestamp": "2026-11-01T00:00:00.000Z"})
        ws = FakeWebSocket(inbound=[cancel, user_message(CHAT_A)])
        await self.run_endpoint(ws)

        # The cancel frame left the socket unbound; the message for CHAT_A bound it there
        self.register.assert_awaited_once()
        origin, chat_id, _ = self.register.await_args.args
        self.assertEqual(chat_id, CHAT_A)
        self.subscribe.assert_awaited_once_with(origin, CHAT_A, ws)
        kwargs = self.process.await_args.kwargs
        self.assertEqual(kwargs["client_chat_id"], CHAT_A)
        self.assertEqual(kwargs["session_id"], f"ws_{USER}_conv_{CHAT_A}")


class TestConnectFailureKeepsSharedContext(EndpointHarness):
    async def test_connect_error_after_context_load_does_not_clear_chat_context(self):
        chat_session = f"ws_{USER}_conv_{CHAT_A}"
        store = {chat_session: ["sibling socket's context"]}

        async def fake_set(session_id, messages):
            store[session_id] = list(messages) or store.get(session_id, [])

        async def fake_clear(session_id):
            store.pop(session_id, None)

        self.set_chat_messages.side_effect = fake_set
        self.register.side_effect = RuntimeError("boom after set_chat_messages")
        ws = FakeWebSocket({"client_chat_id": CHAT_A})
        with patch("app.clear_chat_session", new=AsyncMock(side_effect=fake_clear)) as clear, \
             patch("app.remove_session_timestamp", new=AsyncMock()), \
             patch("app.unsubscribe_from_conversation", new=AsyncMock()), \
             patch("app.remove_user_session", new=AsyncMock()):
            await self.run_endpoint(ws)

        self.set_chat_messages.assert_awaited_once()
        clear.assert_not_awaited()
        self.assertEqual(store[chat_session], ["sibling socket's context"])
        self.assertEqual(json.loads(ws.send_text.await_args.args[0])["code"], "AUTH_GENERAL_ERROR")


class TestPerMessageRouting(EndpointHarness):
    async def test_uuid_message_on_socket_bound_to_other_chat_uses_its_own_chat(self):
        self.resolve.return_value = 100
        self.conv_id_for.side_effect = lambda cid, uid: {CHAT_B: 200}.get(cid)
        ws = FakeWebSocket({"client_chat_id": CHAT_A}, inbound=[user_message(CHAT_B)])
        await self.run_endpoint(ws)

        kwargs = self.process.await_args.kwargs
        self.assertEqual(kwargs["session_id"], f"ws_{USER}_conv_{CHAT_B}")
        self.assertEqual(kwargs["client_chat_id"], CHAT_B)
        self.assertEqual(kwargs["session_conversation_id"], 200)
        # The socket stays bound to its own chat
        self.register.assert_awaited_once()
        self.assertEqual(self.register.await_args.args[1], CHAT_A)

    async def test_message_without_chat_id_uses_connection_chat(self):
        self.resolve.return_value = 100
        ws = FakeWebSocket({"client_chat_id": CHAT_A}, inbound=[user_message(None)])
        await self.run_endpoint(ws)

        kwargs = self.process.await_args.kwargs
        self.assertEqual(kwargs["session_id"], f"ws_{USER}_conv_{CHAT_A}")
        self.assertEqual(kwargs["client_chat_id"], CHAT_A)
        self.assertEqual(kwargs["session_conversation_id"], 100)


class TestTwoSocketsSameChat(unittest.IsolatedAsyncioTestCase):
    """Real registration through the endpoint, then a broadcast excluding socket A's origin."""

    async def test_broadcast_reaches_other_socket_not_originator(self):
        local = LocalObjectManager()
        with ExitStack() as stack:
            p = lambda target, **kw: stack.enter_context(patch(target, **kw))
            p("jose.jwt.decode", return_value={"sub": USER, "email": "u1@example.com"})
            p("app.redis_client", new=None)  # local delivery path only
            p("app.redis_managers", new={"local_objects": local})
            p("app.get_total_session_count", new=AsyncMock(return_value=0))
            p("app.update_session_activity_redis", new=AsyncMock())
            p("app.load_conversation_history", return_value=[])
            p("app.set_chat_messages", new=AsyncMock())
            p("app.add_user_session", new=AsyncMock())
            p("app.evict_user_sessions_if_needed", new=AsyncMock())
            p("app.cleanup_session", new=AsyncMock())  # keep registrations after disconnect
            p("app.get_active_requests", new=AsyncMock(return_value=set()))
            p("app.resolve_conversation_id", return_value=None)

            ws_a = FakeWebSocket({"client_chat_id": CHAT_A})
            ws_b = FakeWebSocket({"client_chat_id": CHAT_A})
            await app_module.websocket_endpoint(ws_a)
            await app_module.websocket_endpoint(ws_b)

            registrations = await local.get_conversation_websockets(CHAT_A)
            self.assertEqual(len(registrations), 2)
            origin_a = next(sid for sid, ws in registrations if ws is ws_a)

            payload = {"type": "broadcast", "response": "hello"}
            await app_module.broadcast_to_conversation_parallel(CHAT_A, payload, exclude_session=origin_a)

        ws_a.send_text.assert_not_awaited()
        ws_b.send_text.assert_awaited_once_with(json.dumps(payload))

    async def test_pubsub_listener_skips_only_its_own_origin(self):
        message = {"type": "message", "data": json.dumps({"payload": {"x": 1}, "exclude_session": "origin-a"})}

        class FakePubSub:
            async def listen(self):
                yield message

        ws_a, ws_b = FakeWebSocket(), FakeWebSocket()
        with patch("app.update_session_activity_redis", new=AsyncMock()):
            await app_module.redis_message_listener("origin-a", FakePubSub(), ws_a)
            await app_module.redis_message_listener("origin-b", FakePubSub(), ws_b)

        ws_a.send_text.assert_not_awaited()
        ws_b.send_text.assert_awaited_once_with(json.dumps({"x": 1}))


if __name__ == "__main__":
    unittest.main()
