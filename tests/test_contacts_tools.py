import json
import os
import sys
import unittest
from unittest.mock import patch, AsyncMock

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import contacts_tools


def store_with(contacts=None, nickname_index=None, **extra):
    data = {"contacts": contacts or {}, "nickname_index": nickname_index or {}}
    data.update(extra)
    return json.dumps(data)


class TestIsImportDeclined(unittest.TestCase):
    def test_declined_candidate_is_reported(self):
        with patch("contacts_tools.get_preference",
                   return_value=store_with(import_declined={"phone:k1": "2026-10-26T00:00:00"})):
            self.assertTrue(contacts_tools.is_import_declined("u1", "phone:k1"))
            self.assertFalse(contacts_tools.is_import_declined("u1", "phone:k2"))

    def test_empty_store_is_not_declined(self):
        with patch("contacts_tools.get_preference", return_value=None):
            self.assertFalse(contacts_tools.is_import_declined("u1", "phone:k1"))


class TestImportOfferBlocked(unittest.TestCase):
    def test_blocked_when_declined(self):
        with patch("contacts_tools.get_preference",
                   return_value=store_with(import_declined={"phone:k1": "t"})):
            self.assertTrue(contacts_tools.import_offer_blocked("u1", "phone:k1", "Jonathan Smith"))

    def test_blocked_when_name_already_saved(self):
        store = store_with(
            contacts={"id1": {"nicknames": ["jonathan smith"], "real_name": "Jonathan Smith",
                              "preferred_app": "sms"}},
            nickname_index={"jonathan smith": "id1"})
        with patch("contacts_tools.get_preference", return_value=store):
            self.assertTrue(contacts_tools.import_offer_blocked("u1", "phone:k1", "Jonathan Smith"))

    def test_not_blocked_otherwise(self):
        with patch("contacts_tools.get_preference", return_value=None):
            self.assertFalse(contacts_tools.import_offer_blocked("u1", "phone:k1", "Jonathan Smith"))


class TestGetContactPreferenceCandidates(unittest.IsolatedAsyncioTestCase):
    async def test_device_candidates_get_candidate_id(self):
        device = {"success": True, "contacts": [
            {"display_name": "Jonathan Smith", "lookup_key": "k1", "phone_numbers": {"mobile": "+1"}}]}
        with patch("contacts_tools.get_preference", return_value=None), \
             patch("device_control_tools.agent_lookup_phone_contacts",
                   new_callable=AsyncMock, return_value=device), \
             patch("contact_import.record_candidates", new_callable=AsyncMock) as rec:
            result = await contacts_tools.get_contact_preference(
                "u1", "Jonny", websocket=object(), conversation_id="c1")
        self.assertFalse(result["found"])
        rec.assert_awaited_once()
        args = rec.await_args.args
        self.assertEqual(args[:4], ("u1", "c1", "Jonny", "phone"))
        self.assertIs(args[4], result["device_contacts"])

    async def test_found_whiz_contact_records_resolved(self):
        store = store_with(
            contacts={"id1": {"nicknames": ["robin pham"], "real_name": "Robin Pham",
                              "preferred_app": "sms", "phone_numbers": {}, "emails": {}, "addresses": {}}},
            nickname_index={"robin pham": "id1"})
        with patch("contacts_tools.get_preference", return_value=store), \
             patch("contact_import.record_resolved", new_callable=AsyncMock) as rec, \
             patch("contact_import.record_candidates", new_callable=AsyncMock) as cands, \
             patch("contacts_tools._google_enrichment_matches", new_callable=AsyncMock,
                   create=True, return_value=[{"display_name": "Robin Pham", "resource_name": "people/1"}]):
            result = await contacts_tools.get_contact_preference(
                "u1", "Robin Pham", conversation_id="c-device", tracking_conversation_id="c-real")
        self.assertTrue(result["found"])
        rec.assert_awaited_once()
        # Tracking uses the real conversation id, not the (possibly optimistic) device one
        self.assertEqual(rec.await_args.args[:3], ("u1", "c-real", "Robin Pham"))
        self.assertEqual(rec.await_args.args[3]["real_name"], "Robin Pham")
        # Google enrichment matches for a contact that IS saved are never import candidates
        cands.assert_not_awaited()

    async def test_device_candidates_use_tracking_conversation_id(self):
        device = {"success": True, "contacts": [{"display_name": "Jonathan Smith", "lookup_key": "k1"}]}
        with patch("contacts_tools.get_preference", return_value=None), \
             patch("device_control_tools.agent_lookup_phone_contacts",
                   new_callable=AsyncMock, return_value=device) as dev, \
             patch("contact_import.record_candidates", new_callable=AsyncMock) as rec:
            await contacts_tools.get_contact_preference(
                "u1", "Jonny", websocket=object(), conversation_id="c-device",
                tracking_conversation_id="c-real")
        self.assertEqual(dev.await_args.args[4], "c-device")
        self.assertEqual(rec.await_args.args[1], "c-real")


class TestPersonToContact(unittest.TestCase):
    def test_person_to_contact_includes_resource_name(self):
        from google_contacts import _person_to_contact
        person = {"resourceName": "people/c1", "names": [{"displayName": "Ann Lee"}],
                  "emailAddresses": [{"value": "ann@x.com", "type": "home"}]}
        contact = _person_to_contact(person)
        self.assertEqual(contact["resource_name"], "people/c1")


CANDIDATE = {
    "display_name": "Jonathan Smith", "source": "phone", "source_id": "k1",
    "phone_numbers": {"mobile": "+1", "work": "+2"}, "primary_phone": "work",
    "emails": {"personal": "a@b"}, "addresses": {}, "queried_as": ["jonny"],
}


class TestImportContact(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.saved = {}

        def fake_set(user_id, key, value):
            self.saved[key] = json.loads(value)
            return True
        self.fake_set = fake_set

    async def run_import(self, store, candidate=CANDIDATE, **kwargs):
        with patch("contacts_tools.get_preference", return_value=store), \
             patch("contacts_tools.set_preference", side_effect=self.fake_set), \
             patch("contact_import.get_candidate", new_callable=AsyncMock, return_value=candidate):
            return await contacts_tools.import_contact("u1", "phone:k1", tracking_conversation_id="c1", **kwargs)

    async def test_import_creates_contact_with_all_fields_and_spoken_nicknames(self):
        result = await self.run_import(None, preferred_app="whatsapp")
        self.assertTrue(result["success"])
        self.assertFalse(result["merged"])
        contacts = self.saved["contacts"]["contacts"]
        self.assertEqual(len(contacts), 1)
        contact = next(iter(contacts.values()))
        self.assertEqual(contact["real_name"], "Jonathan Smith")
        self.assertEqual(contact["preferred_app"], "whatsapp")
        self.assertEqual(contact["phone_numbers"], {"mobile": "+1", "work": "+2"})
        self.assertEqual(contact["primary_phone"], "work")
        self.assertEqual(contact["primary_email"], "personal")
        self.assertEqual(contact["nicknames"], ["jonathan smith", "jonny"])
        self.assertEqual(contact["source"], "phone")
        self.assertEqual(contact["source_id"], "k1")
        self.assertEqual(self.saved["contacts"]["nickname_index"]["jonny"], next(iter(contacts)))
        self.assertEqual(result["contact"]["real_name"], "Jonathan Smith")

    async def test_import_merges_into_existing_contact_without_overwriting(self):
        store = store_with(
            contacts={"id1": {"nicknames": ["jonathan smith"], "real_name": "Jonathan Smith",
                              "preferred_app": "sms", "phone_numbers": {"mobile": "+9"},
                              "primary_phone": "mobile", "emails": {}, "addresses": {}}},
            nickname_index={"jonathan smith": "id1"})
        result = await self.run_import(store, preferred_app="whatsapp", nicknames=["my brother"])
        self.assertTrue(result["merged"])
        contact = self.saved["contacts"]["contacts"]["id1"]
        self.assertEqual(contact["phone_numbers"], {"mobile": "+9", "work": "+2"})
        self.assertEqual(contact["primary_phone"], "mobile")
        self.assertEqual(contact["preferred_app"], "whatsapp")
        self.assertEqual(contact["nicknames"], ["jonathan smith", "brother", "jonny"])

    async def test_import_skips_nickname_owned_by_other_contact(self):
        store = store_with(
            contacts={"other": {"nicknames": ["jonny"], "real_name": "Jon Doe", "preferred_app": "sms",
                                "phone_numbers": {}, "emails": {}, "addresses": {}}},
            nickname_index={"jonny": "other"})
        result = await self.run_import(store, preferred_app="whatsapp")
        self.assertEqual(result["skipped_nicknames"], ["jonny"])
        self.assertEqual(self.saved["contacts"]["nickname_index"]["jonny"], "other")

    async def test_import_unknown_candidate_errors(self):
        result = await self.run_import(None, candidate=None, preferred_app="whatsapp")
        self.assertFalse(result["success"])
        self.assertIn("get_contact_preference", result["error"])
        self.assertEqual(self.saved, {})

    async def test_import_requires_preferred_app(self):
        result = await self.run_import(None)
        self.assertFalse(result["success"])

    async def test_never_ask_again_persists_and_is_import_declined(self):
        result = await self.run_import(None, never_ask_again=True)
        self.assertTrue(result["success"])
        self.assertTrue(result["declined"])
        with patch("contacts_tools.get_preference", return_value=json.dumps(self.saved["contacts"])):
            self.assertTrue(contacts_tools.is_import_declined("u1", "phone:k1"))

    async def test_never_ask_again_reports_save_failure(self):
        with patch("contacts_tools.get_preference", return_value=None), \
             patch("contacts_tools.set_preference", return_value=False):
            result = await contacts_tools.import_contact("u1", "phone:k1", never_ask_again=True)
        self.assertFalse(result["success"])


class TestImportContactSchema(unittest.TestCase):
    def test_schema_registered(self):
        names = [t["name"] for t in contacts_tools.contacts_tools]
        self.assertIn("import_contact", names)
        schema = next(t for t in contacts_tools.contacts_tools if t["name"] == "import_contact")
        self.assertEqual(schema["input_schema"]["required"], ["candidate_id"])
        self.assertIn("never_ask_again", schema["input_schema"]["properties"])
        self.assertNotIn("decline", schema["input_schema"]["properties"])


if __name__ == "__main__":
    unittest.main()
