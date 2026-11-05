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


class TestGuardedMutatingTools(unittest.TestCase):
    """Each externally visible, non-idempotent tool gets its own matcher."""

    def setUp(self):
        self.redis = FakeRedis()
        tool_dedupe.init_redis_client(self.redis)

    def record(self, tool, args, result=None):
        asyncio.run(tool_dedupe.record_call(
            tool, args, "u1", result if result is not None else {"status": "success"}, "r1"))

    def find(self, tool, args):
        return asyncio.run(tool_dedupe.find_recent_similar(tool, args, "u1"))

    def assertDuplicate(self, tool, first, second=None):
        self.record(tool, first)
        self.assertIsNotNone(self.find(tool, dict(second if second is not None else first)), tool)

    def assertDistinct(self, tool, first, second):
        self.record(tool, first)
        self.assertIsNone(self.find(tool, dict(second)), tool)

    # --- message sends (bug reports 1507, 1543) ---
    def test_send_same_app_same_text_is_duplicate(self):
        self.assertDuplicate("agent_send_message",
                             {"app": "whatsapp", "message": "Lee's jacket and glasses are gone from his bag."})

    def test_send_lightly_reworded_text_is_duplicate(self):
        self.assertDuplicate("agent_send_message",
                             {"app": "whatsapp", "message": "Lee's jacket and glasses are gone from his bag"},
                             {"app": "whatsapp", "message": "Lee's jacket & glasses are gone from his bag!"})

    def test_send_different_text_is_distinct(self):
        self.assertDistinct("agent_send_message",
                            {"app": "whatsapp", "message": "Running ten minutes late"},
                            {"app": "whatsapp", "message": "Can you grab milk on the way home"})

    def test_send_different_app_is_distinct(self):
        self.assertDistinct("agent_send_message",
                            {"app": "whatsapp", "message": "Running ten minutes late"},
                            {"app": "sms", "message": "Running ten minutes late"})

    def test_send_result_with_status_error_is_not_recorded(self):
        args = {"app": "whatsapp", "message": "Running ten minutes late"}
        self.record("agent_send_message", args,
                    result={"status": "error", "message": "Timeout waiting for device"})
        self.assertIsNone(self.find("agent_send_message", dict(args)))

    # --- bug reports (1468) ---
    def test_bug_report_same_text_is_duplicate(self):
        self.assertDuplicate("agent_submit_bug_report", {"message": "Wake word didn't work three times"})

    def test_bug_report_different_text_is_distinct(self):
        self.assertDistinct("agent_submit_bug_report",
                            {"message": "Wake word didn't work three times"},
                            {"message": "Maps start button was not pressed"})

    # --- health logging (1467) ---
    def test_health_same_type_value_date_is_duplicate(self):
        self.assertDuplicate("agent_log_health_data",
                             {"data_type": "weight", "value": 139, "date": "2026-07-11"})

    def test_health_value_as_string_vs_number_is_duplicate(self):
        self.assertDuplicate("agent_log_health_data",
                             {"data_type": "weight", "value": 139},
                             {"data_type": "weight", "value": "139"})

    def test_health_different_value_is_distinct(self):
        self.assertDistinct("agent_log_health_data",
                            {"data_type": "calories", "value": 500},
                            {"data_type": "calories", "value": 650})

    def test_health_different_date_is_distinct(self):
        self.assertDistinct("agent_log_health_data",
                            {"data_type": "weight", "value": 139, "date": "2026-07-10"},
                            {"data_type": "weight", "value": 139, "date": "2026-07-11"})

    # --- alarms and timers ---
    def test_alarm_same_time_is_duplicate(self):
        self.assertDuplicate("agent_set_alarm", {"hour": 7, "minute": 30, "label": "gym"},
                             {"hour": 7, "minute": 30})

    def test_alarm_different_time_is_distinct(self):
        self.assertDistinct("agent_set_alarm", {"hour": 7, "minute": 30}, {"hour": 7, "minute": 45})

    def test_timer_same_duration_is_duplicate(self):
        self.assertDuplicate("agent_set_timer", {"seconds": 600})

    def test_timer_different_duration_is_distinct(self):
        self.assertDistinct("agent_set_timer", {"seconds": 600}, {"seconds": 300})

    # --- phone calls ---
    def test_dial_same_number_different_formatting_is_duplicate(self):
        self.assertDuplicate("agent_dial_phone_number",
                             {"phone_number": "415-604-8024"}, {"phone_number": "(415) 604 8024"})

    def test_dial_different_number_is_distinct(self):
        self.assertDistinct("agent_dial_phone_number",
                            {"phone_number": "415-604-8024"}, {"phone_number": "415-604-9999"})

    def test_press_call_button_twice_is_duplicate(self):
        self.assertDuplicate("agent_press_call_button", {}, {"expected_number": "4156048024"})

    # --- calendar: only saving is irreversible ---
    def test_calendar_save_same_title_and_start_is_duplicate(self):
        self.assertDuplicate("agent_calendar_event",
                             {"action": "save", "title": "Dentist", "begin_time": "2026-11-02T14:00:00"})

    def test_calendar_save_different_start_is_distinct(self):
        self.assertDistinct("agent_calendar_event",
                            {"action": "save", "title": "Dentist", "begin_time": "2026-11-02T14:00:00"},
                            {"action": "save", "title": "Dentist", "begin_time": "2026-11-03T14:00:00"})

    def test_calendar_draft_is_never_duplicate(self):
        self.assertDistinct("agent_calendar_event",
                            {"action": "draft", "title": "Dentist", "begin_time": "2026-11-02T14:00:00"},
                            {"action": "draft", "title": "Dentist", "begin_time": "2026-11-02T14:00:00"})

    def test_calendar_save_after_draft_is_not_duplicate(self):
        self.assertDistinct("agent_calendar_event",
                            {"action": "draft", "title": "Dentist", "begin_time": "2026-11-02T14:00:00"},
                            {"action": "save", "title": "Dentist", "begin_time": "2026-11-02T14:00:00"})

    # --- music: only queueing stacks up; play/play_next are retries or intent ---
    def test_queue_same_query_is_duplicate(self):
        self.assertDuplicate("agent_youtube_music", {"action": "queue", "query": "Seven Nation Army"})

    def test_queue_different_query_is_distinct(self):
        self.assertDistinct("agent_youtube_music",
                            {"action": "queue", "query": "Seven Nation Army"},
                            {"action": "queue", "query": "Blue Orchid"})

    def test_play_twice_is_never_duplicate(self):
        self.assertDistinct("agent_youtube_music",
                            {"action": "play", "query": "Seven Nation Army"},
                            {"action": "play", "query": "Seven Nation Army"})

    def test_play_next_twice_is_never_duplicate(self):
        self.assertDistinct("agent_youtube_music",
                            {"action": "play_next", "query": "Seven Nation Army"},
                            {"action": "play_next", "query": "Seven Nation Army"})

    # --- contacts ---
    def test_import_same_candidate_is_duplicate(self):
        self.assertDuplicate("import_contact", {"candidate_id": "phone:42", "nicknames": ["husband"]},
                             {"candidate_id": "phone:42"})

    def test_import_different_candidate_is_distinct(self):
        self.assertDistinct("import_contact", {"candidate_id": "phone:42"}, {"candidate_id": "phone:43"})

    def test_add_contact_same_name_is_duplicate(self):
        self.assertDuplicate("add_contact_preference",
                             {"real_name": "Robin Pham", "preferred_app": "whatsapp"},
                             {"real_name": "robin pham", "preferred_app": "sms"})

    def test_add_contact_different_name_is_distinct(self):
        self.assertDistinct("add_contact_preference",
                            {"real_name": "Robin Pham", "preferred_app": "whatsapp"},
                            {"real_name": "Asya Dovlatov", "preferred_app": "whatsapp"})

    # --- idempotent screen tools stay unguarded ---
    def test_idempotent_tools_are_not_guarded(self):
        for tool in ("agent_draft_message", "agent_app_control", "agent_get_google_maps_directions",
                     "agent_click", "agent_set_volume", "agent_dismiss_alarm", "update_asana_task"):
            self.assertFalse(tool_dedupe.is_guarded(tool), tool)

    # --- warning wording ---
    def test_send_warning_says_sent_not_created(self):
        entry = {"args": {"app": "whatsapp", "message": "hi"}, "result_summary": {"status": "success"},
                 "ts": 1000.0, "request_id": "r"}
        warning = tool_dedupe.build_duplicate_warning("agent_send_message", entry, now=1004.0)
        self.assertIn("message", warning["message"])
        self.assertIn("sent", warning["message"])
        self.assertNotIn("created", warning["message"])
        self.assertIn("confirm_duplicate", warning["how_to_proceed"])


if __name__ == "__main__":
    unittest.main()
