import unittest
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from asana_tools import asana_tools


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


if __name__ == "__main__":
    unittest.main()
