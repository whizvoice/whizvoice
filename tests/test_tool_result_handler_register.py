import asyncio
import json
import os
import sys
import unittest
from unittest.mock import AsyncMock

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tool_result_handler import ToolResultHandler
import messaging_tools


class TestRegisterPendingTool(unittest.TestCase):
    """Bug 1625: agent_dismiss_draft calls register_pending_tool, which did not exist."""

    def test_register_then_wait_returns_result_delivered_before_wait_started(self):
        handler = ToolResultHandler()

        async def scenario():
            handler.register_pending_tool("req_1", "agent_dismiss_draft")
            # Device answers before the server reaches wait_for_tool_result.
            self.assertTrue(handler.handle_tool_result("req_1", {"status": "success"}))
            return await handler.wait_for_tool_result("req_1", timeout=1.0)

        result = asyncio.run(scenario())
        self.assertEqual(result, {"status": "success"})
        self.assertEqual(handler.get_pending_count(), 0)

    def test_register_then_wait_still_times_out_when_nothing_arrives(self):
        handler = ToolResultHandler()

        async def scenario():
            handler.register_pending_tool("req_2", "agent_dismiss_draft")
            return await handler.wait_for_tool_result("req_2", timeout=0.05)

        result = asyncio.run(scenario())
        self.assertEqual(result["status"], "timeout")
        self.assertEqual(handler.get_pending_count(), 0)

    def test_dismiss_draft_returns_device_result_instead_of_attribute_error(self):
        handler = ToolResultHandler()
        websocket = AsyncMock()

        async def answer_when_sent(message_json):
            request_id = json.loads(message_json)["request_id"]
            handler.handle_tool_result(request_id, {"status": "success", "dismissed": True})

        websocket.send_text.side_effect = answer_when_sent

        result = asyncio.run(messaging_tools.agent_dismiss_draft(
            user_id="u1", websocket=websocket, tool_result_handler=handler, conversation_id="c1"))

        self.assertEqual(result, {"status": "success", "dismissed": True})


if __name__ == "__main__":
    unittest.main()
