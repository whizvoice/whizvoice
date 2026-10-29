import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import device_control_tools
from app import execute_tool


class TestRageShakeTool(unittest.TestCase):
    def _run(self, args):
        with patch.object(device_control_tools, "_send_device_tool", new=AsyncMock(return_value={"success": True})) as send:
            asyncio.run(execute_tool("agent_snooze_rage_shake", args, "user1", websocket=object()))
            return send.call_args.args[1]  # params dict forwarded to the device

    def test_mode_off_is_forwarded_to_device(self):
        self.assertEqual(self._run({"mode": "off"}), {"mode": "off"})

    def test_mode_on_is_forwarded_to_device(self):
        self.assertEqual(self._run({"mode": "on"}), {"mode": "on"})

    def test_missing_mode_defaults_to_snooze(self):
        self.assertEqual(self._run({}), {"mode": "snooze"})

    def test_unknown_mode_is_rejected(self):
        with patch.object(device_control_tools, "_send_device_tool", new=AsyncMock(return_value={"success": True})) as send:
            result = asyncio.run(execute_tool("agent_snooze_rage_shake", {"mode": "sideways"}, "user1", websocket=object()))
        self.assertIn("error", result)
        send.assert_not_called()

    def test_schema_advertises_three_modes(self):
        tool = next(t for t in device_control_tools.device_control_tools if t["name"] == "agent_snooze_rage_shake")
        self.assertEqual(tool["input_schema"]["properties"]["mode"]["enum"], ["snooze", "off", "on"])


if __name__ == "__main__":
    unittest.main()
