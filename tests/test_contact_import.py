import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import contact_import


class FakeRedis:
    def __init__(self):
        self.store = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value


SENT = {"status": "sent"}


class TestContactImport(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.redis = FakeRedis()
        contact_import.init_redis_client(self.redis)
        self.phone = {"display_name": "Jonathan Smith", "lookup_key": "k1",
                      "phone_numbers": {"mobile": "+1"}}

    async def record_phone(self):
        await contact_import.record_candidates("u1", "c1", "Jonny", "phone", [self.phone])

    async def test_record_candidates_assigns_ids_and_stores_snapshot(self):
        await self.record_phone()
        self.assertEqual(self.phone["candidate_id"], "phone:k1")
        snap = await contact_import.get_candidate("u1", "c1", "phone:k1")
        self.assertEqual(snap["display_name"], "Jonathan Smith")
        self.assertEqual(snap["source"], "phone")
        self.assertEqual(snap["source_id"], "k1")
        self.assertEqual(snap["queried_as"], ["jonny"])

    async def test_record_candidates_falls_back_to_display_name_without_lookup_key(self):
        contact = {"display_name": "Jonathan Smith"}
        await contact_import.record_candidates("u1", "c1", "Jonny", "phone", [contact])
        self.assertEqual(contact["candidate_id"], "phone:Jonathan Smith")

    async def test_record_candidates_noop_without_conversation_id(self):
        contact = {"display_name": "Jonathan Smith", "lookup_key": "k1"}
        await contact_import.record_candidates("u1", None, "Jonny", "phone", [contact])
        self.assertEqual(self.redis.store, {})
        self.assertNotIn("candidate_id", contact)

    async def test_observe_draft_then_send_attaches_hint_once(self):
        await self.record_phone()
        await contact_import.observe("agent_draft_message",
                                     {"app": "whatsapp", "contact_name": "Jonny", "message": "hi"},
                                     "u1", "c1", {"status": "drafted"})
        first = await contact_import.observe("agent_send_message", {"app": "whatsapp", "message": "hi"},
                                             "u1", "c1", dict(SENT))
        hint = first["suggest_import"]
        self.assertEqual(hint["candidate_id"], "phone:k1")
        self.assertEqual(hint["used_app"], "whatsapp")
        self.assertEqual(hint["spoken_as"], ["jonny"])
        self.assertEqual(hint["display_name"], "Jonathan Smith")
        self.assertIn("import_contact", hint["instruction"])
        self.assertIn("never_ask_again", hint["instruction"])
        self.assertNotIn("decline=true", hint["instruction"])
        second = await contact_import.observe("agent_send_message", {"app": "whatsapp", "message": "again"},
                                              "u1", "c1", dict(SENT))
        self.assertNotIn("suggest_import", second)

    async def test_observe_send_failure_does_not_consume_offer(self):
        await self.record_phone()
        await contact_import.observe("agent_whatsapp_draft_message",
                                     {"contact_name": "Jonathan Smith", "message": "hi"},
                                     "u1", "c1", {})
        failed = await contact_import.observe("agent_whatsapp_send_message", {"message": "hi"},
                                              "u1", "c1", {"error": "boom", "success": False})
        self.assertNotIn("suggest_import", failed)
        ok = await contact_import.observe("agent_whatsapp_send_message", {"message": "hi"},
                                          "u1", "c1", dict(SENT))
        self.assertIn("suggest_import", ok)

    async def test_observe_send_skips_blocked_candidate(self):
        # import_offer_blocked covers "declined before" and "already saved in Whiz"
        await self.record_phone()
        await contact_import.observe("agent_select_chat", {"app": "sms", "contact_name": "Jonny"},
                                     "u1", "c1", {})
        with patch("contacts_tools.import_offer_blocked", return_value=True) as blocked:
            result = await contact_import.observe("agent_sms_send_message", {"message": "hi"},
                                                  "u1", "c1", dict(SENT))
        self.assertNotIn("suggest_import", result)
        blocked.assert_called_once_with("u1", "phone:k1", "Jonathan Smith")

    async def test_record_candidates_skips_candidate_without_display_name(self):
        contact = {"display_name": None, "resource_name": "otherContacts/x", "emails": {"other": "a@b"}}
        await contact_import.record_candidates("u1", "c1", "Ann", "google", [contact])
        self.assertNotIn("candidate_id", contact)
        self.assertIsNone(await contact_import.get_candidate("u1", "c1", "google:otherContacts/x"))

    async def test_multiple_matches_do_not_record_spoken_name(self):
        a = {"display_name": "Jon Smith", "lookup_key": "a"}
        b = {"display_name": "Jon Smithers", "lookup_key": "b"}
        await contact_import.record_candidates("u1", "c1", "Jon", "phone", [a, b])
        self.assertEqual((await contact_import.get_candidate("u1", "c1", "phone:a"))["queried_as"], [])
        await contact_import.observe("agent_draft_message",
                                     {"app": "sms", "contact_name": "Jon", "message": "hi"},
                                     "u1", "c1", {})
        no_hint = await contact_import.observe("agent_send_message", {"app": "sms", "message": "hi"},
                                               "u1", "c1", dict(SENT))
        self.assertNotIn("suggest_import", no_hint)
        await contact_import.observe("agent_draft_message",
                                     {"app": "sms", "contact_name": "Jon Smith", "message": "hi"},
                                     "u1", "c1", {})
        hint = await contact_import.observe("agent_send_message", {"app": "sms", "message": "hi"},
                                            "u1", "c1", dict(SENT))
        self.assertEqual(hint["suggest_import"]["candidate_id"], "phone:a")

    async def test_resolved_spoken_names_do_not_bleed_between_contacts(self):
        await contact_import.record_resolved("u1", "c1", "mom",
                                             {"real_name": "Jane Doe", "preferred_app": "sms",
                                              "nicknames": ["mom"]})
        await contact_import.record_resolved("u1", "c1", "boss",
                                             {"real_name": "Bob Lee", "preferred_app": "whatsapp",
                                              "nicknames": ["boss"]})
        await contact_import.observe("agent_draft_message",
                                     {"app": "sms", "contact_name": "Mom", "message": "hi"},
                                     "u1", "c1", {})
        jane = await contact_import.observe("agent_send_message", {"app": "sms", "message": "hi"},
                                            "u1", "c1", dict(SENT))
        self.assertNotIn("suggest_update_preferred_app", jane)
        await contact_import.observe("agent_draft_message",
                                     {"app": "sms", "contact_name": "boss", "message": "hi"},
                                     "u1", "c1", {})
        bob = await contact_import.observe("agent_send_message", {"app": "sms", "message": "hi"},
                                           "u1", "c1", dict(SENT))
        self.assertEqual(bob["suggest_update_preferred_app"]["real_name"], "Bob Lee")

    async def test_resolved_contact_wins_over_candidate_with_same_spoken_name(self):
        await contact_import.record_resolved("u1", "c1", "Jonny",
                                             {"real_name": "Jonathan Smith", "preferred_app": "whatsapp",
                                              "nicknames": ["jonny"]})
        await self.record_phone()
        await contact_import.observe("agent_draft_message",
                                     {"app": "whatsapp", "contact_name": "Jonny", "message": "hi"},
                                     "u1", "c1", {})
        result = await contact_import.observe("agent_send_message", {"app": "whatsapp", "message": "hi"},
                                              "u1", "c1", dict(SENT))
        self.assertNotIn("suggest_import", result)
        self.assertNotIn("suggest_update_preferred_app", result)

    async def test_observe_noop_without_conversation_id(self):
        result = await contact_import.observe("agent_send_message", {"app": "whatsapp"},
                                              "u1", None, dict(SENT))
        self.assertEqual(result, SENT)
        self.assertEqual(self.redis.store, {})

    async def test_observe_resolved_contact_on_other_app_suggests_update(self):
        await contact_import.record_resolved("u1", "c1", "Robin",
                                             {"real_name": "Robin Pham", "preferred_app": "sms",
                                              "nicknames": ["husband"]})
        await contact_import.observe("agent_draft_message",
                                     {"app": "whatsapp", "contact_name": "Robin Pham", "message": "hi"},
                                     "u1", "c1", {})
        result = await contact_import.observe("agent_send_message", {"app": "whatsapp", "message": "hi"},
                                              "u1", "c1", dict(SENT))
        hint = result["suggest_update_preferred_app"]
        self.assertEqual(hint["real_name"], "Robin Pham")
        self.assertEqual(hint["current_preferred_app"], "sms")
        self.assertEqual(hint["used_app"], "whatsapp")
        again = await contact_import.observe("agent_send_message", {"app": "whatsapp", "message": "hi"},
                                             "u1", "c1", dict(SENT))
        self.assertNotIn("suggest_update_preferred_app", again)

    async def test_observe_resolved_contact_on_same_app_no_hint(self):
        await contact_import.record_resolved("u1", "c1", "Robin",
                                             {"real_name": "Robin Pham", "preferred_app": "sms",
                                              "nicknames": []})
        await contact_import.observe("agent_draft_message",
                                     {"app": "sms", "contact_name": "Robin", "message": "hi"},
                                     "u1", "c1", {})
        result = await contact_import.observe("agent_send_message", {"app": "sms", "message": "hi"},
                                              "u1", "c1", dict(SENT))
        self.assertNotIn("suggest_update_preferred_app", result)
        self.assertNotIn("suggest_import", result)

    async def test_observe_non_dict_result_passthrough(self):
        await self.record_phone()
        await contact_import.observe("agent_draft_message",
                                     {"app": "whatsapp", "contact_name": "Jonny", "message": "hi"},
                                     "u1", "c1", {})
        self.assertEqual(await contact_import.observe("agent_send_message", {"app": "whatsapp"},
                                                      "u1", "c1", "ok"), "ok")

    async def test_observe_without_redis_is_passthrough(self):
        contact_import.init_redis_client(None)
        result = await contact_import.observe("agent_send_message", {"app": "whatsapp"},
                                              "u1", "c1", dict(SENT))
        self.assertEqual(result, SENT)


if __name__ == "__main__":
    unittest.main()
