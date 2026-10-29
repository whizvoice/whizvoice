"""
Contact import tracking.

When get_contact_preference misses in Whiz but finds a phone/Google contact,
the candidate snapshot is recorded here, per conversation, in Redis. The
observer watches select/draft/send tool calls as they pass through
execute_tool_with_queue and, after a successful WhatsApp/SMS send to such a
contact, attaches a `suggest_import` hint to the send result so the model
offers to save the contact exactly once. When a saved Whiz contact was used on
the *other* app, a `suggest_update_preferred_app` hint is attached instead.

The snapshot is also what `import_contact` reads, so the import needs no
second round-trip to the device or the People API.

Every public coroutine swallows and logs: this module must never break a
tool call.
"""

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_redis_client = None

KEY_TTL_SECONDS = 24 * 3600
MESSAGING_APPS = {"whatsapp", "sms"}

TARGET_TOOLS = {
    "agent_select_chat", "agent_draft_message",
    "agent_whatsapp_select_chat", "agent_whatsapp_draft_message",
    "agent_sms_select_chat", "agent_sms_draft_message",
}
SEND_TOOLS = {"agent_send_message", "agent_whatsapp_send_message", "agent_sms_send_message"}

_RESOLVED_OFFERED = "resolved"


def init_redis_client(client):
    """Called once at startup with the shared async redis client."""
    global _redis_client
    _redis_client = client


def candidate_id(source: str, source_id: str) -> str:
    return f"{source}:{source_id}"


def _key(user_id: str, conversation_id) -> str:
    return f"contact_import:{user_id}:{conversation_id}"


def _enabled(user_id, conversation_id) -> bool:
    return _redis_client is not None and bool(user_id) and conversation_id is not None


def _empty_state() -> Dict[str, Any]:
    # resolved: normalized real_name -> saved Whiz contact summary (one slot per contact,
    # so spoken names never bleed from one saved contact to another).
    return {"queries": [], "candidates": {}, "resolved": {}, "active": None, "offered": []}


async def _load(user_id, conversation_id) -> Dict[str, Any]:
    raw = await _redis_client.get(_key(user_id, conversation_id))
    if not raw:
        return _empty_state()
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    state = _empty_state()
    state.update(json.loads(raw))
    if not isinstance(state.get("resolved"), dict):
        state["resolved"] = {}
    return state


async def _save(user_id, conversation_id, state: Dict[str, Any]) -> None:
    await _redis_client.set(_key(user_id, conversation_id), json.dumps(state), ex=KEY_TTL_SECONDS)


def _normalize(name) -> str:
    from contacts_tools import normalize_nickname
    return normalize_nickname(name or "")


def _source_id_for(source: str, contact: Dict[str, Any]) -> Optional[str]:
    if source == "phone":
        sid = contact.get("lookup_key")
    elif source == "google":
        sid = contact.get("resource_name")
    else:
        sid = None
    # Older app builds don't send lookup_key; fall back to the display name so
    # the contact can still be imported from its snapshot.
    return sid or contact.get("display_name")


async def record_candidates(user_id, conversation_id, queried_name, source: str,
                            contacts: List[Dict[str, Any]]) -> None:
    """Snapshot phone/Google matches and tag each with a candidate_id (in place)."""
    if not _enabled(user_id, conversation_id) or not contacts:
        return
    try:
        state = await _load(user_id, conversation_id)
        spoken = _normalize(queried_name)
        if spoken and spoken not in state["queries"]:
            state["queries"].append(spoken)
        # Only a sole match may claim the spoken name: with several matches we can't
        # know which one "Jon" meant, and a wrong claim would later become a nickname.
        sole_match = len(contacts) == 1
        for contact in contacts:
            if not contact.get("display_name"):
                continue
            source_id = _source_id_for(source, contact)
            if not source_id:
                continue
            cid = candidate_id(source, source_id)
            contact["candidate_id"] = cid
            snapshot = state["candidates"].get(cid) or {}
            queried_as = snapshot.get("queried_as", [])
            if spoken and sole_match and spoken not in queried_as:
                queried_as.append(spoken)
            snapshot.update({k: v for k, v in contact.items() if k != "candidate_id"})
            snapshot.update({"source": source, "source_id": source_id, "queried_as": queried_as})
            state["candidates"][cid] = snapshot
        await _save(user_id, conversation_id, state)
    except Exception as e:
        logger.warning(f"contact_import.record_candidates failed: {e}")


async def record_resolved(user_id, conversation_id, queried_name, contact: Dict[str, Any]) -> None:
    """Remember that the lookup resolved to a saved Whiz contact."""
    if not _enabled(user_id, conversation_id) or not contact:
        return
    try:
        state = await _load(user_id, conversation_id)
        spoken = _normalize(queried_name)
        key = _normalize(contact.get("real_name"))
        if not key:
            return
        entry = state["resolved"].get(key) or {"queried_as": []}
        if spoken and spoken not in entry["queried_as"]:
            entry["queried_as"].append(spoken)
        entry.update({
            "real_name": contact.get("real_name"),
            "preferred_app": contact.get("preferred_app"),
            "nicknames": list(contact.get("nicknames") or []),
        })
        state["resolved"][key] = entry
        await _save(user_id, conversation_id, state)
    except Exception as e:
        logger.warning(f"contact_import.record_resolved failed: {e}")


async def get_candidate(user_id, conversation_id, cid: str) -> Optional[Dict[str, Any]]:
    if not _enabled(user_id, conversation_id):
        return None
    try:
        state = await _load(user_id, conversation_id)
        return state["candidates"].get(cid)
    except Exception as e:
        logger.warning(f"contact_import.get_candidate failed: {e}")
        return None


def _app_for(tool_name: str, tool_args: Dict[str, Any]) -> Optional[str]:
    app = (tool_args or {}).get("app")
    if not app:
        if "whatsapp" in tool_name:
            app = "whatsapp"
        elif "sms" in tool_name:
            app = "sms"
    return app if app in MESSAGING_APPS else None


def _match_candidate(state: Dict[str, Any], name: str) -> Optional[str]:
    candidates = state["candidates"]
    for cid, snap in candidates.items():
        if _normalize(snap.get("display_name")) == name:
            return cid
    for cid, snap in candidates.items():
        if name in snap.get("queried_as", []):
            return cid
    return None


def _match_resolved(state: Dict[str, Any], name: str) -> Optional[str]:
    """Key of the saved Whiz contact the name refers to, if any."""
    resolved = state["resolved"]
    if name in resolved:
        return name
    for key, entry in resolved.items():
        if name in entry.get("nicknames", []) or name in entry.get("queried_as", []):
            return key
    return None


def _succeeded(result: Any) -> bool:
    return isinstance(result, dict) and "error" not in result and result.get("success") is not False


async def _note_target(state, tool_name, tool_args) -> bool:
    app = _app_for(tool_name, tool_args)
    name = _normalize(tool_args.get("contact_name"))
    if not app or not name:
        return False
    # A saved Whiz contact wins over a phone/Google candidate with the same name:
    # the lookup may have surfaced Google matches purely to enrich a saved contact.
    resolved_key = _match_resolved(state, name)
    if resolved_key:
        state["active"] = {"resolved": resolved_key, "app": app}
        return True
    cid = _match_candidate(state, name)
    state["active"] = {"candidate_id": cid, "app": app} if cid else None
    return True


def _import_instruction(display_name: str, app: str) -> str:
    return (
        f"The message was sent to a contact that is not saved in Whiz. After confirming it was "
        f"sent, ask once, in one short sentence, whether to save {display_name} as a {app} contact. "
        f"If yes, call import_contact with this candidate_id and preferred_app='{app}'. "
        f"If no, just acknowledge; only if they say never to ask about this contact again, "
        f"call import_contact with this candidate_id and never_ask_again=true. "
        f"Do not ask again in this conversation."
    )


def _update_instruction(real_name: str, current: str, used: str) -> str:
    return (
        f"{real_name} is saved with {current} as preferred app but this message went via {used}. "
        f"Ask once, in one short sentence, whether to switch; if yes call add_contact_preference "
        f"with real_name='{real_name}' and preferred_app='{used}'. Do not ask again in this conversation."
    )


async def _annotate_send(state, user_id, tool_name, tool_args, result: Dict[str, Any]) -> bool:
    app = _app_for(tool_name, tool_args)
    active = state.get("active")
    if not app or not active:
        return False
    offered = state["offered"]
    if active.get("candidate_id"):
        cid = active["candidate_id"]
        snap = state["candidates"].get(cid)
        if not snap or cid in offered:
            return False
        from contacts_tools import import_offer_blocked
        if import_offer_blocked(user_id, cid, snap.get("display_name")):
            return False
        result["suggest_import"] = {
            "candidate_id": cid,
            "display_name": snap.get("display_name"),
            "source": snap.get("source"),
            "used_app": app,
            "spoken_as": snap.get("queried_as", []),
            "instruction": _import_instruction(snap.get("display_name"), app),
        }
        offered.append(cid)
        return True
    key = active.get("resolved")
    resolved = state["resolved"].get(key) if key else None
    if not resolved:
        return False
    current = resolved.get("preferred_app")
    flag = f"{_RESOLVED_OFFERED}:{key}"
    if current and current != app and flag not in offered:
        result["suggest_update_preferred_app"] = {
            "real_name": resolved.get("real_name"),
            "current_preferred_app": current,
            "used_app": app,
            "instruction": _update_instruction(resolved.get("real_name"), current, app),
        }
        offered.append(flag)
        return True
    return False


async def observe(tool_name: str, tool_args: Dict[str, Any], user_id, conversation_id, result: Any) -> Any:
    """Watch a tool call; returns `result`, annotated with a hint when due."""
    if not _enabled(user_id, conversation_id):
        return result
    if tool_name not in TARGET_TOOLS and tool_name not in SEND_TOOLS:
        return result
    try:
        state = await _load(user_id, conversation_id)
        if tool_name in TARGET_TOOLS:
            changed = await _note_target(state, tool_name, tool_args or {})
        else:
            if not _succeeded(result):
                return result
            changed = await _annotate_send(state, user_id, tool_name, tool_args or {}, result)
        if changed:
            await _save(user_id, conversation_id, state)
    except Exception as e:
        logger.warning(f"contact_import.observe failed for {tool_name}: {e}")
    return result
