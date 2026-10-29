import unittest
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import send_gate


def user_text(text):
    return {"role": "user", "content": [{"type": "text", "text": text}]}


def assistant_text(text):
    return {"role": "assistant", "content": [{"type": "text", "text": text}]}


def assistant_tool_use(tool_id, name, **inputs):
    return {"role": "assistant", "content": [{"type": "tool_use", "id": tool_id, "name": name, "input": inputs}]}


def tool_result(tool_id, content='{"status": "success", "overlay_shown": true}'):
    return {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": content}]}


FILLER = "[system: previous tool result returned above; no new message from the user]"


class TestSendIsConfirmed(unittest.TestCase):
    """The user must have said something after the draft overlay appeared."""

    def test_bug_1543_send_in_same_turn_as_draft_is_not_confirmed(self):
        messages = [
            user_text("Tell him Lee's jacket and glasses are gone from his bag"),
            assistant_tool_use("d1", "agent_draft_message", app="whatsapp", message="Lee's jacket..."),
            tool_result("d1"),
            assistant_text("Would you like me to add that to the draft, or send separately?"),
            user_text("Any"),
            assistant_tool_use("d2", "agent_draft_message", app="whatsapp", message="Lee's jacket... glasses yet."),
            tool_result("d2"),
        ]
        self.assertFalse(send_gate.send_is_confirmed(messages))

    def test_user_reply_after_draft_is_confirmed(self):
        messages = [
            user_text("Tell him we need both bikes"),
            assistant_tool_use("d1", "agent_draft_message", app="whatsapp", message="We need both bikes"),
            tool_result("d1"),
            assistant_text("Draft ready! Just say send when you're ready."),
            user_text("Send it"),
        ]
        self.assertTrue(send_gate.send_is_confirmed(messages))

    def test_server_filler_after_draft_is_not_a_reply(self):
        messages = [
            user_text("Tell him we need both bikes"),
            assistant_tool_use("d1", "agent_draft_message", app="whatsapp", message="We need both bikes"),
            tool_result("d1"),
            user_text(FILLER),
        ]
        self.assertFalse(send_gate.send_is_confirmed(messages))

    def test_whitespace_only_user_text_is_not_a_reply(self):
        messages = [
            assistant_tool_use("d1", "agent_draft_message", app="sms", message="hi"),
            tool_result("d1"),
            user_text("   "),
        ]
        self.assertFalse(send_gate.send_is_confirmed(messages))

    def test_reply_merged_into_same_user_message_as_draft_result_counts(self):
        # database.py merges consecutive USER messages: tool_result first, then text.
        messages = [
            assistant_tool_use("d1", "agent_draft_message", app="sms", message="hi"),
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "d1", "content": '{"status": "success"}'},
                {"type": "text", "text": "yes send it"},
            ]},
        ]
        self.assertTrue(send_gate.send_is_confirmed(messages))

    def test_reply_to_an_older_draft_does_not_confirm_a_newer_one(self):
        messages = [
            assistant_tool_use("d1", "agent_draft_message", app="sms", message="first version"),
            tool_result("d1"),
            user_text("Change it to say second version"),
            assistant_tool_use("d2", "agent_draft_message", app="sms", message="second version"),
            tool_result("d2"),
        ]
        self.assertFalse(send_gate.send_is_confirmed(messages))

    def test_legacy_whatsapp_and_sms_draft_tools_are_recognized(self):
        for name in ("agent_whatsapp_draft_message", "agent_sms_draft_message"):
            messages = [assistant_tool_use("d1", name, message="hi"), tool_result("d1")]
            self.assertFalse(send_gate.send_is_confirmed(messages), name)

    def test_no_draft_in_history_is_left_alone(self):
        # The draft requirement is enforced by the tool description, not here;
        # a truncated history must not block a legitimate send.
        messages = [user_text("send it"), assistant_text("ok")]
        self.assertTrue(send_gate.send_is_confirmed(messages))

    def test_plain_string_user_content_counts_as_a_reply(self):
        messages = [
            assistant_tool_use("d1", "agent_draft_message", app="sms", message="hi"),
            tool_result("d1"),
            {"role": "user", "content": "send it"},
        ]
        self.assertTrue(send_gate.send_is_confirmed(messages))

    def test_other_tool_results_after_the_draft_do_not_count(self):
        messages = [
            assistant_tool_use("d1", "agent_draft_message", app="whatsapp", message="hi"),
            tool_result("d1"),
            assistant_tool_use("c1", "get_contact_preference", nickname="husband"),
            tool_result("c1", '{"found": true}'),
        ]
        self.assertFalse(send_gate.send_is_confirmed(messages))

    def test_send_tools_are_the_three_send_variants(self):
        self.assertEqual(send_gate.SEND_TOOLS,
                         {"agent_send_message", "agent_whatsapp_send_message", "agent_sms_send_message"})


class TestBuildRefusal(unittest.TestCase):
    def test_refusal_is_an_error_that_tells_the_model_to_ask(self):
        refusal = send_gate.build_refusal("agent_send_message")
        self.assertIn("error", refusal)
        self.assertFalse(refusal["executed"])
        self.assertTrue(refusal["send_blocked"])
        self.assertIn("NOT SENT", refusal["error"])
        self.assertIn("confirm", refusal["how_to_proceed"].lower())
        self.assertIn("agent_send_message", refusal["how_to_proceed"])


if __name__ == "__main__":
    unittest.main()
