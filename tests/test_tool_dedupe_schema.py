import unittest
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from asana_tools import asana_tools
# Imported at collection time, before any test has run asyncio.run(): app.py's
# import chain creates an asyncio.Lock on a Python 3.9 default loop, which
# fails once an earlier test has closed that loop.
import tool_dedupe
from app import tools, tools_parent_required


class TestConfirmDuplicateSchema(unittest.TestCase):
    def setUp(self):
        self.create_tool = next(t for t in asana_tools if t["name"] == "get_new_asana_task_id")

    def test_confirm_duplicate_is_advertised(self):
        props = self.create_tool["input_schema"]["properties"]
        self.assertIn("confirm_duplicate", props)
        self.assertEqual(props["confirm_duplicate"]["type"], "boolean")

    def test_confirm_duplicate_is_not_required(self):
        self.assertNotIn("confirm_duplicate", self.create_tool["input_schema"]["required"])

    def test_description_tells_model_when_to_use_it(self):
        desc = self.create_tool["input_schema"]["properties"]["confirm_duplicate"]["description"]
        self.assertIn("duplicate_warning", desc)


class TestAllGuardedToolsAdvertiseConfirmDuplicate(unittest.TestCase):
    """The guard answers with 'call again with confirm_duplicate=true'; that
    only works if every guarded tool's advertised schema accepts the flag."""

    def setUp(self):
        self.guarded = set(tool_dedupe.GUARDS)
        self.tool_lists = {"tools": tools, "tools_parent_required": tools_parent_required}

    def test_every_guarded_tool_is_advertised(self):
        advertised = {t["name"] for t in self.tool_lists["tools"]}
        self.assertEqual(self.guarded - advertised, set())

    def test_every_guarded_tool_accepts_confirm_duplicate(self):
        for list_name, tool_list in self.tool_lists.items():
            for t in tool_list:
                if t["name"] not in self.guarded:
                    continue
                props = t["input_schema"]["properties"]
                self.assertIn("confirm_duplicate", props, f"{t['name']} in {list_name}")
                self.assertEqual(props["confirm_duplicate"]["type"], "boolean")
                self.assertIn("duplicate_warning", props["confirm_duplicate"]["description"])
                self.assertNotIn("confirm_duplicate", t["input_schema"].get("required", []))

    def test_unguarded_tools_do_not_get_the_flag(self):
        for t in self.tool_lists["tools"]:
            if t["name"] in self.guarded:
                continue
            self.assertNotIn("confirm_duplicate", t["input_schema"]["properties"], t["name"])


if __name__ == "__main__":
    unittest.main()
