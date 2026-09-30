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


# Tools guarded by this module, and how to compare two calls to them.
# Add an entry here to extend the guard to another mutating tool.
GUARDS = {
    "get_new_asana_task_id": {
        "matcher": _match_asana_create,
        "summarizer": _summarize_asana_create,
        "noun": "task",
    },
}


def is_guarded(tool_name: str) -> bool:
    return tool_name in GUARDS


def _key(tool_name: str, user_id: str) -> str:
    return f"recent_tool_calls:{user_id}:{tool_name}"


def _succeeded(result: Any) -> bool:
    """Only successful calls are recorded. Recording a failure would let the
    guard talk a later request out of a mutation that never happened."""
    if isinstance(result, dict):
        return "error" not in result
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
    seconds_ago = max(0, int(now - entry.get("ts", now)))
    return {
        "duplicate_warning": True,
        "executed": False,
        "message": (
            f"NOT EXECUTED. A very similar {noun} was already created successfully "
            f"{seconds_ago} seconds ago, shown below. This usually means the user's "
            f"request was processed twice (for example they repeated themselves while "
            f"the first request was still running), and creating another would leave a "
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
            f"Otherwise do not call it again — tell the user the {noun} is already added, "
            f"referring to the existing one above."
        ),
    }
