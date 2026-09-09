import unittest
from unittest.mock import patch, AsyncMock
import asyncio
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import execute_tool_with_queue

ARGS = {
    "name": "Check if chapter spot mailing addresses update on Salesforce",
    "parent_task_gid": "1204993766187118",
}


class TestDuplicateGuardIntegration(unittest.TestCase):
    def run_tool(self, args):
        return asyncio.run(execute_tool_with_queue(
            "get_new_asana_task_id", args, "user_1", request_id="req_1"))

    @patch("app.tool_dedupe.record_call", new_callable=AsyncMock)
    @patch("app.tool_dedupe.find_recent_similar", new_callable=AsyncMock)
    @patch("app.execute_tool", new_callable=AsyncMock)
    def test_executes_and_records_when_no_recent_match(self, mock_exec, mock_find, mock_record):
        mock_find.return_value = None
        mock_exec.return_value = {"gid": "111", "name": ARGS["name"]}

        result = self.run_tool(dict(ARGS))

        self.assertEqual(result["gid"], "111")
        mock_exec.assert_awaited_once()
        mock_record.assert_awaited_once()

    @patch("app.tool_dedupe.record_call", new_callable=AsyncMock)
    @patch("app.tool_dedupe.find_recent_similar", new_callable=AsyncMock)
    @patch("app.execute_tool", new_callable=AsyncMock)
    def test_returns_warning_without_executing_on_match(self, mock_exec, mock_find, mock_record):
        mock_find.return_value = {
            "args": dict(ARGS),
            "result_summary": {"gid": "999", "name": ARGS["name"]},
            "ts": 1000.0,
            "request_id": "other_req",
        }

        result = self.run_tool(dict(ARGS))

        self.assertTrue(result["duplicate_warning"])
        self.assertFalse(result["executed"])
        self.assertEqual(result["existing_call"]["result"]["gid"], "999")
        mock_exec.assert_not_awaited()
        mock_record.assert_not_awaited()

    @patch("app.tool_dedupe.record_call", new_callable=AsyncMock)
    @patch("app.tool_dedupe.find_recent_similar", new_callable=AsyncMock)
    @patch("app.execute_tool", new_callable=AsyncMock)
    def test_confirm_duplicate_bypasses_guard(self, mock_exec, mock_find, mock_record):
        mock_exec.return_value = {"gid": "222", "name": ARGS["name"]}

        result = self.run_tool({**ARGS, "confirm_duplicate": True})

        self.assertEqual(result["gid"], "222")
        mock_find.assert_not_awaited()
        mock_exec.assert_awaited_once()

    @patch("app.tool_dedupe.record_call", new_callable=AsyncMock)
    @patch("app.tool_dedupe.find_recent_similar", new_callable=AsyncMock)
    @patch("app.execute_tool", new_callable=AsyncMock)
    def test_confirm_duplicate_is_stripped_before_dispatch(self, mock_exec, mock_find, mock_record):
        mock_exec.return_value = {"gid": "222", "name": ARGS["name"]}

        self.run_tool({**ARGS, "confirm_duplicate": True})

        dispatched_args = mock_exec.await_args.args[1]
        self.assertNotIn("confirm_duplicate", dispatched_args)
        self.assertEqual(dispatched_args["name"], ARGS["name"])

    @patch("app.tool_dedupe.record_call", new_callable=AsyncMock)
    @patch("app.tool_dedupe.find_recent_similar", new_callable=AsyncMock)
    @patch("app.execute_tool", new_callable=AsyncMock)
    def test_unguarded_tool_skips_lookup_entirely(self, mock_exec, mock_find, mock_record):
        mock_exec.return_value = [{"gid": "1"}]

        asyncio.run(execute_tool_with_queue("get_asana_tasks", {}, "user_1"))

        mock_find.assert_not_awaited()
        mock_exec.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
