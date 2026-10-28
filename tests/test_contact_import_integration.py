import asyncio
import os
import sys
import unittest
from unittest.mock import patch, AsyncMock

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import execute_tool_with_queue, execute_tool, TOOL_REGISTRY

SEND_ARGS = {"app": "whatsapp", "message": "hi"}


class TestContactImportIntegration(unittest.TestCase):
    def test_queue_executes_tools_through_observer(self):
        # agent_* tools run via the screen-agent queue (immediately or deferred);
        # the queue's executor must be the observed wrapper so deferred sends
        # still get their hint.
        # The registration happens in the startup handler (needs Redis), so guard
        # the wiring at source level: nothing else may be registered as executor.
        import inspect, app
        src = inspect.getsource(app)
        self.assertIn("set_execute_tool_func(_execute_tool_observed)", src)
        self.assertNotIn("set_execute_tool_func(execute_tool)", src)

    @patch("app.contact_import.observe", new_callable=AsyncMock)
    @patch("app.execute_tool", new_callable=AsyncMock)
    def test_observed_executor_uses_tracking_conversation_id(self, mock_exec, mock_observe):
        from app import _execute_tool_observed
        sent = {"status": "sent"}
        mock_exec.return_value = sent
        mock_observe.return_value = {"status": "sent", "suggest_import": {"candidate_id": "phone:k1"}}

        result = asyncio.run(_execute_tool_observed(
            "agent_send_message", dict(SEND_ARGS), user_id="u1",
            conversation_id=-17, tracking_conversation_id=42, device_id="d1"))

        self.assertEqual(result["suggest_import"]["candidate_id"], "phone:k1")
        mock_observe.assert_awaited_once_with("agent_send_message", SEND_ARGS, "u1", 42, sent)

    def test_import_contact_registered_and_validated(self):
        self.assertIn("import_contact", TOOL_REGISTRY)
        missing = asyncio.run(execute_tool("import_contact", {}, user_id="u1"))
        self.assertIn("error", missing)
        bad_app = asyncio.run(execute_tool("import_contact", {"candidate_id": "phone:k1"}, user_id="u1"))
        self.assertIn("error", bad_app)

    @patch("app.import_contact", new_callable=AsyncMock)
    def test_import_contact_dispatch_passes_conversation_id(self, mock_import):
        mock_import.return_value = {"success": True}
        asyncio.run(execute_tool("import_contact",
                                 {"candidate_id": "phone:k1", "never_ask_again": True},
                                 user_id="u1", conversation_id="c1"))
        mock_import.assert_awaited_once_with("u1", "phone:k1", None, None, True, "c1")

    @patch("app.import_contact", new_callable=AsyncMock)
    def test_import_contact_dispatch_prefers_tracking_conversation_id(self, mock_import):
        mock_import.return_value = {"success": True}
        asyncio.run(execute_tool("import_contact",
                                 {"candidate_id": "phone:k1", "never_ask_again": True},
                                 user_id="u1", conversation_id=-17, tracking_conversation_id=42))
        mock_import.assert_awaited_once_with("u1", "phone:k1", None, None, True, 42)

    def test_get_contact_preference_dispatch_passes_both_ids(self):
        mapping = TOOL_REGISTRY["get_contact_preference"]["args_mapping"]
        args = mapping({"name": "Jonny"}, "u1", websocket="ws", tool_result_handler="trh",
                       conversation_id=-17, tracking_conversation_id=42)
        self.assertEqual(args[4], -17)
        self.assertEqual(args[-1], 42)


if __name__ == "__main__":
    unittest.main()
