import unittest
import asyncio
import json
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tool_dedupe


class FakeRedis:
    """Minimal async stand-in for the sorted-set + expire calls tool_dedupe uses."""

    def __init__(self):
        self.zsets = {}
        self.fail = False

    async def zadd(self, key, mapping):
        if self.fail:
            raise RuntimeError("redis down")
        self.zsets.setdefault(key, {}).update(mapping)

    async def zremrangebyscore(self, key, min_score, max_score):
        if self.fail:
            raise RuntimeError("redis down")
        z = self.zsets.get(key, {})
        for member in [m for m, s in z.items() if min_score <= s <= max_score]:
            del z[member]

    async def zrangebyscore(self, key, min_score, max_score):
        if self.fail:
            raise RuntimeError("redis down")
        z = self.zsets.get(key, {})
        return [m for m, s in sorted(z.items(), key=lambda kv: kv[1]) if min_score <= s <= max_score]

    async def expire(self, key, seconds):
        if self.fail:
            raise RuntimeError("redis down")


class TestToolDedupe(unittest.TestCase):
    def setUp(self):
        self.redis = FakeRedis()
        tool_dedupe.init_redis_client(self.redis)

    def record(self, args, result=None, user_id="u1", request_id="r1"):
        asyncio.run(tool_dedupe.record_call(
            "get_new_asana_task_id", args, user_id,
            result if result is not None else {"gid": "111", "name": args["name"]},
            request_id,
        ))

    def find(self, args, user_id="u1"):
        return asyncio.run(tool_dedupe.find_recent_similar("get_new_asana_task_id", args, user_id))

    def test_identical_args_match(self):
        args = {"name": "Check if chapter spot mailing addresses update on Salesforce",
                "parent_task_gid": "1204993766187118"}
        self.record(args)
        self.assertIsNotNone(self.find(dict(args)))

    def test_reworded_title_same_parent_matches(self):
        self.record({"name": "Check if doctor finder website is up to date with cache of addresses",
                     "parent_task_gid": "1211010999662462"})
        hit = self.find({"name": "Check if doctor-finding website is up to date with cache of addresses",
                         "parent_task_gid": "1211010999662462"})
        self.assertIsNotNone(hit)

    def test_unrelated_title_same_parent_does_not_match(self):
        self.record({"name": "Check if chapter spot mailing addresses update on Salesforce",
                     "parent_task_gid": "1204993766187118"})
        hit = self.find({"name": "Book flights to Denver for the March conference",
                         "parent_task_gid": "1204993766187118"})
        self.assertIsNone(hit)

    def test_different_parent_does_not_match(self):
        self.record({"name": "Check if chapter spot mailing addresses update on Salesforce",
                     "parent_task_gid": "1204993766187118"})
        hit = self.find({"name": "Check if chapter spot mailing addresses update on Salesforce",
                         "parent_task_gid": "9999999999"})
        self.assertIsNone(hit)

    def test_different_user_does_not_match(self):
        args = {"name": "Check if chapter spot mailing addresses update on Salesforce",
                "parent_task_gid": "1204993766187118"}
        self.record(args, user_id="u1")
        self.assertIsNone(self.find(dict(args), user_id="u2"))

    def test_outside_window_does_not_match(self):
        args = {"name": "Check if chapter spot mailing addresses update on Salesforce",
                "parent_task_gid": "1204993766187118"}
        self.record(args)
        key = tool_dedupe._key("get_new_asana_task_id", "u1")
        stale = {m: s - (tool_dedupe.WINDOW_SECONDS + 10) for m, s in self.redis.zsets[key].items()}
        self.redis.zsets[key] = stale
        self.assertIsNone(self.find(dict(args)))

    def test_failed_call_is_not_recorded(self):
        args = {"name": "Check if chapter spot mailing addresses update on Salesforce",
                "parent_task_gid": "1204993766187118"}
        self.record(args, result={"error": "Asana API error.", "status_code": 500})
        self.assertIsNone(self.find(dict(args)))

    def test_unguarded_tool_is_never_matched(self):
        self.assertFalse(tool_dedupe.is_guarded("get_asana_tasks"))
        asyncio.run(tool_dedupe.record_call("get_asana_tasks", {}, "u1", [{"gid": "1"}], "r1"))
        self.assertIsNone(asyncio.run(
            tool_dedupe.find_recent_similar("get_asana_tasks", {}, "u1")))

    def test_redis_failure_degrades_open(self):
        args = {"name": "Check if chapter spot mailing addresses update on Salesforce",
                "parent_task_gid": "1204993766187118"}
        self.record(args)
        self.redis.fail = True
        self.assertIsNone(self.find(dict(args)))
        self.record(args)  # must not raise

    def test_no_redis_client_degrades_open(self):
        tool_dedupe.init_redis_client(None)
        args = {"name": "Anything at all", "parent_task_gid": "1"}
        self.record(args)  # must not raise
        self.assertIsNone(self.find(dict(args)))

    def test_build_duplicate_warning_shape(self):
        entry = {
            "args": {"name": "Check if chapter spot mailing addresses update on Salesforce",
                     "parent_task_gid": "1204993766187118"},
            "result_summary": {"gid": "1217343724288397",
                               "name": "Check if chapter spot mailing addresses update on Salesforce"},
            "ts": 1000.0,
            "request_id": "dc4126aa",
        }
        warning = tool_dedupe.build_duplicate_warning("get_new_asana_task_id", entry, now=1002.0)
        self.assertTrue(warning["duplicate_warning"])
        self.assertEqual(warning["seconds_ago"], 2)
        self.assertEqual(warning["existing_call"]["args"], entry["args"])
        self.assertEqual(warning["existing_call"]["result"], entry["result_summary"])
        self.assertIn("confirm_duplicate", warning["how_to_proceed"])


if __name__ == "__main__":
    unittest.main()
