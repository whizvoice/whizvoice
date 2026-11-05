"""Pre-execution duplicate guard for mutating tools.

Concurrent requests in one conversation run independent agent loops, so two
models can independently decide to perform the same mutation before either
result exists in shared context (see the 2026-08-10 duplicate Asana task
incidents). Context propagation cannot fix that — at decision time the
information does not exist yet. This module closes the gap at execution time
instead: successful mutating calls are recorded in Redis with a short TTL, and
a later similar call is answered with a synthetic "are you sure?" tool result
rather than being executed.

The guard asks rather than blocks. A wrong match costs one extra model turn; a
wrong block would silently drop a task the user asked for. That asymmetry is
why the matching below is deliberately loose.
"""

import json
import logging
import time
from difflib import SequenceMatcher
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_redis_client = None

WINDOW_SECONDS = 60
SIMILARITY_THRESHOLD = 0.6
_MAX_ENTRIES_SCANNED = 20


def init_redis_client(client):
    """Set the Redis client used to share recent-call state across workers."""
    global _redis_client
    _redis_client = client


def _normalize(text: str) -> str:
    """Lowercase and strip non-alphanumerics so punctuation and hyphenation
    differences ('doctor finder' vs 'doctor-finding') do not defeat matching."""
    return "".join(ch for ch in (text or "").lower() if ch.isalnum() or ch == " ").strip()


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def _match_asana_create(new_args: Dict, old_args: Dict) -> bool:
    """Same parent and a roughly similar title. Parent must match exactly:
    the same title under a different parent is a deliberate, different task."""
    if (new_args.get("parent_task_gid") or None) != (old_args.get("parent_task_gid") or None):
        return False
    return _similar(new_args.get("name", ""), old_args.get("name", "")) >= SIMILARITY_THRESHOLD


def _summarize_asana_create(result: Any) -> Any:
    if isinstance(result, dict):
        return {"gid": result.get("gid"), "name": result.get("name")}
    return result


def _summarize_status(result: Any) -> Any:
    """Screen-agent results are small status dicts; keep the shape, drop bulk."""
    if isinstance(result, dict):
        return {k: v for k, v in result.items() if k in ("status", "success", "action", "message")}
    return result


def _digits(text: Any) -> str:
    return "".join(ch for ch in str(text or "") if ch.isdigit())


def _same(new_args: Dict, old_args: Dict, *fields: str) -> bool:
    """Exact equality on each field, compared as normalized strings so that
    139 and "139" (or a missing field and None) are the same value."""
    return all(str(new_args.get(f) if new_args.get(f) is not None else "").strip().lower()
               == str(old_args.get(f) if old_args.get(f) is not None else "").strip().lower()
               for f in fields)


def _text_similar(field: str):
    def matcher(new_args: Dict, old_args: Dict) -> bool:
        return _similar(new_args.get(field, ""), old_args.get(field, "")) >= SIMILARITY_THRESHOLD
    return matcher


def _match_send_message(new_args: Dict, old_args: Dict) -> bool:
    """Same app and roughly the same text. A different app is a different
    recipient channel, which is a deliberate second send."""
    return _same(new_args, old_args, "app") and _text_similar("message")(new_args, old_args)


def _match_health_log(new_args: Dict, old_args: Dict) -> bool:
    return _same(new_args, old_args, "data_type", "value", "date")


def _match_alarm(new_args: Dict, old_args: Dict) -> bool:
    return _same(new_args, old_args, "hour", "minute")


def _match_timer(new_args: Dict, old_args: Dict) -> bool:
    return _same(new_args, old_args, "seconds")


def _match_dial(new_args: Dict, old_args: Dict) -> bool:
    return _digits(new_args.get("phone_number")) == _digits(old_args.get("phone_number"))


def _match_any(new_args: Dict, old_args: Dict) -> bool:
    """Pressing call twice within the window is always the same call."""
    return True


def _match_calendar_save(new_args: Dict, old_args: Dict) -> bool:
    """Only a save is irreversible; drafts are previews and may repeat freely.
    A save that follows a draft is the normal flow, not a duplicate."""
    if new_args.get("action") != "save" or old_args.get("action") != "save":
        return False
    return (_same(new_args, old_args, "begin_time")
            and _text_similar("title")(new_args, old_args))


def _match_music_queue(new_args: Dict, old_args: Dict) -> bool:
    """Queueing stacks the same track twice. Play is a retry (the prompt tells
    the model to retry once) and play_next twice is a deliberate repeat."""
    if new_args.get("action") != "queue" or old_args.get("action") != "queue":
        return False
    return _text_similar("query")(new_args, old_args)


def _match_import_contact(new_args: Dict, old_args: Dict) -> bool:
    return _same(new_args, old_args, "candidate_id")


# Tools guarded by this module, and how to compare two calls to them.
# Add an entry here to extend the guard to another mutating tool. Only tools
# whose repeat is visible to someone else or leaves a duplicate record belong
# here; idempotent screen actions (launch, draft, navigate, play) are retried
# on purpose and must stay out.
#   matcher:    (new_args, old_args) -> bool
#   summarizer: result -> compact result to show the model
#   noun/verb:  wording of the warning, "A very similar {noun} was already {verb}"
GUARDS = {
    "get_new_asana_task_id": {
        "matcher": _match_asana_create,
        "summarizer": _summarize_asana_create,
        "noun": "task",
        "verb": "created",
    },
    "agent_send_message": {
        "matcher": _match_send_message,
        "summarizer": _summarize_status,
        "noun": "message",
        "verb": "sent",
    },
    "agent_submit_bug_report": {
        "matcher": _text_similar("message"),
        "summarizer": _summarize_status,
        "noun": "bug report",
        "verb": "submitted",
    },
    "agent_log_health_data": {
        "matcher": _match_health_log,
        "summarizer": _summarize_status,
        "noun": "health entry",
        "verb": "logged",
    },
    "agent_set_alarm": {
        "matcher": _match_alarm,
        "summarizer": _summarize_status,
        "noun": "alarm",
        "verb": "set",
    },
    "agent_set_timer": {
        "matcher": _match_timer,
        "summarizer": _summarize_status,
        "noun": "timer",
        "verb": "set",
    },
    "agent_dial_phone_number": {
        "matcher": _match_dial,
        "summarizer": _summarize_status,
        "noun": "number",
        "verb": "dialed",
    },
    "agent_press_call_button": {
        "matcher": _match_any,
        "summarizer": _summarize_status,
        "noun": "call",
        "verb": "placed",
    },
    "agent_calendar_event": {
        "matcher": _match_calendar_save,
        "summarizer": _summarize_status,
        "noun": "calendar event",
        "verb": "saved",
    },
    "agent_youtube_music": {
        "matcher": _match_music_queue,
        "summarizer": _summarize_status,
        "noun": "track",
        "verb": "queued",
    },
    "import_contact": {
        "matcher": _match_import_contact,
        "summarizer": _summarize_status,
        "noun": "contact",
        "verb": "imported",
    },
    "add_contact_preference": {
        "matcher": _text_similar("real_name"),
        "summarizer": _summarize_status,
        "noun": "contact",
        "verb": "added",
    },
}

CONFIRM_DUPLICATE_PROPERTY = {
    "type": "boolean",
    "description": (
        "Leave this out on a normal call. Only set it to true after a previous call "
        "returned duplicate_warning and you are confident the user genuinely wants "
        "this as a separate, additional action on top of the one already done."
    ),
}


def advertise_confirm_duplicate(tool_list):
    """Add the confirm_duplicate flag to the advertised schema of every guarded
    tool so the model can act on build_duplicate_warning's how_to_proceed.
    Mutates and returns the list; idempotent."""
    for tool in tool_list:
        if tool.get("name") in GUARDS:
            props = tool.setdefault("input_schema", {}).setdefault("properties", {})
            props.setdefault("confirm_duplicate", dict(CONFIRM_DUPLICATE_PROPERTY))
    return tool_list


def is_guarded(tool_name: str) -> bool:
    return tool_name in GUARDS


def _key(tool_name: str, user_id: str) -> str:
    return f"recent_tool_calls:{user_id}:{tool_name}"


def _succeeded(result: Any) -> bool:
    """Only successful calls are recorded. Recording a failure would let the
    guard talk a later request out of a mutation that never happened."""
    if isinstance(result, dict):
        # Screen-agent tools report failure as {"status": "error"} without an
        # "error" key (e.g. a device timeout), so check both shapes.
        return "error" not in result and str(result.get("status", "")).lower() != "error"
    if isinstance(result, str):
        return not result.startswith("Error")
    return result is not None


async def record_call(tool_name: str, tool_args: Dict, user_id: str,
                      result: Any, request_id: Optional[str]) -> None:
    """Record a successful guarded call. Never raises."""
    if not is_guarded(tool_name) or not user_id or _redis_client is None:
        return
    if not _succeeded(result):
        return
    try:
        now = time.time()
        entry = json.dumps({
            "args": tool_args,
            "result_summary": GUARDS[tool_name]["summarizer"](result),
            "ts": now,
            "request_id": request_id,
        })
        key = _key(tool_name, user_id)
        await _redis_client.zadd(key, {entry: now})
        await _redis_client.zremrangebyscore(key, 0, now - WINDOW_SECONDS)
        await _redis_client.expire(key, WINDOW_SECONDS * 2)
    except Exception as e:
        logger.warning(f"tool_dedupe.record_call failed for {tool_name}: {e}")


async def find_recent_similar(tool_name: str, tool_args: Dict,
                              user_id: str) -> Optional[Dict]:
    """Return the most recent similar successful call within the window, or None.
    Never raises — on any failure the caller proceeds with normal execution."""
    if not is_guarded(tool_name) or not user_id or _redis_client is None:
        return None
    try:
        now = time.time()
        key = _key(tool_name, user_id)
        raw = await _redis_client.zrangebyscore(key, now - WINDOW_SECONDS, now)
        matcher = GUARDS[tool_name]["matcher"]
        for item in reversed(raw[-_MAX_ENTRIES_SCANNED:]):
            entry = json.loads(item)
            if matcher(tool_args, entry.get("args", {})):
                return entry
        return None
    except Exception as e:
        logger.warning(f"tool_dedupe.find_recent_similar failed for {tool_name}: {e}")
        return None


def build_duplicate_warning(tool_name: str, entry: Dict, now: float) -> Dict:
    """The synthetic tool result returned instead of executing."""
    noun = GUARDS[tool_name]["noun"]
    verb = GUARDS[tool_name].get("verb", "created")
    seconds_ago = max(0, int(now - entry.get("ts", now)))
    return {
        "duplicate_warning": True,
        "executed": False,
        "message": (
            f"NOT EXECUTED. A very similar {noun} was already {verb} successfully "
            f"{seconds_ago} seconds ago, shown below. This usually means the user's "
            f"request was processed twice (for example they repeated themselves while "
            f"the first request was still running), and doing it again would leave a "
            f"duplicate. Assume it is the same {noun} unless you have a concrete reason "
            f"to think otherwise."
        ),
        "seconds_ago": seconds_ago,
        "existing_call": {
            "args": entry.get("args"),
            "result": entry.get("result_summary"),
        },
        "how_to_proceed": (
            f"If this really is a separate {noun} the user wants in addition to the one "
            f"above, call {tool_name} again with confirm_duplicate=true and it will run. "
            f"Otherwise do not call it again — tell the user the {noun} was already {verb}, "
            f"referring to the existing one above."
        ),
    }
