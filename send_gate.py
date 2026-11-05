"""Structural confirmation gate for message sends.

agent_send_message's description tells the model it must have the user's explicit
confirmation after drafting. Bug report 1543 (2026-08-28) showed that instruction
being skipped outright: the model called agent_draft_message and agent_send_message
in the same turn, with no user message in between, then the user's real "send it"
sent the message a second time.

That case is detectable without any judgment about what the user said: a send with
no user message after the draft overlay appeared is never legitimate, because the
user cannot have confirmed something they had not yet seen. This module checks that
shape against the conversation as sent to the model and refuses the send when it
holds. It does not try to judge whether the user's words were actually a "yes";
that remains the model's call (bug 1622 is that other failure).
"""

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

SEND_TOOLS = {"agent_send_message", "agent_whatsapp_send_message", "agent_sms_send_message"}
DRAFT_TOOLS = {"agent_draft_message", "agent_whatsapp_draft_message", "agent_sms_draft_message"}

# Server-generated USER fillers (app.py's concurrent-request-lap guard) are not
# something the user said.
_FILLER_PREFIX = "[system:"


def _is_user_speech(block: Any) -> bool:
    if isinstance(block, str):
        text = block
    elif isinstance(block, dict) and block.get("type") == "text":
        text = block.get("text", "")
    else:
        return False
    text = (text or "").strip()
    return bool(text) and not text.startswith(_FILLER_PREFIX)


def send_is_confirmed(messages: List[Dict]) -> bool:
    """True unless the most recent draft overlay has had no user speech after it.

    Walks the Claude-format message list in order. A draft "happens" at its
    tool_result block (that is when the overlay is on screen). Any non-filler
    user text after that block, including text merged into the same user message
    after the tool_result, counts as the user having replied.
    """
    draft_ids = set()
    confirmed = True  # no draft seen yet -> nothing to confirm
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content", [])
        blocks = content if isinstance(content, list) else [content]
        for block in blocks:
            if role == "assistant":
                if (isinstance(block, dict) and block.get("type") == "tool_use"
                        and block.get("name") in DRAFT_TOOLS and block.get("id")):
                    draft_ids.add(block["id"])
            elif role == "user":
                if (isinstance(block, dict) and block.get("type") == "tool_result"
                        and block.get("tool_use_id") in draft_ids):
                    confirmed = False
                elif _is_user_speech(block):
                    confirmed = True
    return confirmed


def build_refusal(tool_name: str) -> Dict:
    """The synthetic tool result returned instead of sending."""
    return {
        "error": (
            "NOT SENT. The draft overlay was just shown and the user has not said "
            "anything since, so they cannot have confirmed it. A send is only allowed "
            "in a later turn, after the user has replied."
        ),
        "executed": False,
        "send_blocked": True,
        "how_to_proceed": (
            f"End this turn now by asking the user to confirm (for example \"Send it?\"). "
            f"Do not call {tool_name} again until the user's next message is an explicit "
            f"yes such as \"send it\" or \"yes\". If their reply is unclear, ask again "
            f"rather than guessing."
        ),
    }
