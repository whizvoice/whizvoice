"""
Event-driven auto-fix trigger with 10-minute debounce.

When a screen agent error is uploaded, schedule_autofix_trigger() starts a
10-minute timer. If more errors arrive before the timer fires, the timer
resets. When it finally fires, it triggers the GitHub Actions workflow via
the GitHub REST API, so all accumulated errors are processed in one run.
"""

import asyncio
import logging
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

DEBOUNCE_SECONDS = 600  # 10 minutes
GITHUB_REPO = "whizvoice/whizvoice"
WORKFLOW_FILE = "screen-agent-autofix.yml"
_PAT_FILE = Path(__file__).parent / "whizvoice-autofix-personal-access-token.txt"

_trigger_task: Optional[asyncio.Task] = None
_trigger_lock = asyncio.Lock()
_last_triggered_at: Optional[float] = None


async def schedule_autofix_trigger():
    """Cancel any pending trigger and start a new 10-minute debounce timer."""
    global _trigger_task
    async with _trigger_lock:
        if _trigger_task and not _trigger_task.done():
            _trigger_task.cancel()
            logger.info("Reset autofix debounce timer")
        _trigger_task = asyncio.create_task(_debounced_trigger())
        logger.info(f"Scheduled autofix trigger (debounce: {DEBOUNCE_SECONDS}s)")


async def _debounced_trigger():
    """Wait for the debounce period, then trigger the GitHub Action."""
    global _last_triggered_at
    try:
        now = asyncio.get_event_loop().time()
        if _last_triggered_at is not None:
            elapsed = now - _last_triggered_at
            if elapsed < DEBOUNCE_SECONDS:
                remaining = DEBOUNCE_SECONDS - elapsed
                logger.info(f"Debouncing autofix trigger for {remaining:.0f}s")
                await asyncio.sleep(remaining)
        # else: first trigger or >10min since last — fire immediately
    except asyncio.CancelledError:
        return

    try:
        github_pat = _PAT_FILE.read_text().strip()
    except FileNotFoundError:
        logger.error(f"GitHub PAT file not found: {_PAT_FILE}")
        return
    if not github_pat:
        logger.error("GitHub PAT file is empty — cannot trigger autofix workflow")
        return

    url = (
        f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/"
        f"{WORKFLOW_FILE}/dispatches"
    )
    headers = {
        "Authorization": f"Bearer {github_pat}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    body = {"ref": "main"}

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, headers=headers, json=body, timeout=30)
        if resp.status_code == 204:
            _last_triggered_at = asyncio.get_event_loop().time()
            logger.info("Successfully triggered autofix workflow")
        else:
            logger.error(
                f"Failed to trigger autofix workflow: "
                f"{resp.status_code} {resp.text}"
            )
    except Exception:
        logger.exception("Error triggering autofix workflow")
