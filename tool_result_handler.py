"""
Handler for tracking pending tool executions and their results.
Uses in-memory storage with asyncio Futures for synchronous tool execution.
"""
import asyncio
import contextvars
import logging
import time
from typing import Dict, Any, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Contextvars for threading tool_use_id and session_id through async call chain
# Set by execute_single_tool in app.py, read by wait_for_tool_result to store metadata
_current_tool_use_id = contextvars.ContextVar('current_tool_use_id', default=None)
_current_session_id = contextvars.ContextVar('current_session_id', default=None)

class ToolResultHandler:
    """Manages pending tool executions and their results."""

    def __init__(self):
        # Maps request_id to asyncio.Future
        self.pending_executions: Dict[str, asyncio.Future] = {}
        # Track creation time for cleanup
        self.execution_times: Dict[str, datetime] = {}
        # Deadline-based timeout tracking (monotonic clock)
        self._deadlines: Dict[str, float] = {}
        # Metadata for mapping request_id -> {tool_use_id, session_id}
        self._metadata: Dict[str, Dict[str, Optional[str]]] = {}

    async def wait_for_tool_result(self, request_id: str, timeout: float = 10.0) -> Dict[str, Any]:
        """
        Wait for a tool execution result with deadline-based timeout.
        Uses asyncio.wait (not wait_for) so the Future is NOT cancelled on timeout,
        allowing the deadline to be extended by extend_deadline().

        Args:
            request_id: Unique ID for the tool execution request
            timeout: Maximum seconds to wait for result

        Returns:
            Tool execution result or timeout error
        """
        # Create a Future for this execution
        future = asyncio.Future()
        self.pending_executions[request_id] = future
        self.execution_times[request_id] = datetime.now()

        # Store metadata from contextvars for later use by extend_deadline
        self._metadata[request_id] = {
            'tool_use_id': _current_tool_use_id.get(None),
            'session_id': _current_session_id.get(None)
        }

        # Set initial deadline
        self._deadlines[request_id] = time.monotonic() + timeout

        try:
            while True:
                remaining = self._deadlines[request_id] - time.monotonic()
                if remaining <= 0:
                    logger.warning(f"Timeout waiting for tool result (request_id: {request_id})")
                    return {
                        "status": "timeout",
                        "error": "Device did not respond within timeout period",
                        "timeout_seconds": timeout
                    }

                # Wait for up to 1 second at a time, then re-check deadline
                # asyncio.wait does NOT cancel the Future on timeout (unlike wait_for)
                done, _ = await asyncio.wait({future}, timeout=min(remaining, 1.0))
                if done:
                    result = future.result()
                    logger.info(f"Received tool result for request {request_id}: {result}")
                    return result
                # Loop re-checks deadline (which may have been extended)

        except Exception as e:
            logger.error(f"Error waiting for tool result: {str(e)}")
            return {
                "status": "error",
                "error": f"Error waiting for tool result: {str(e)}"
            }

        finally:
            # Clean up
            self.pending_executions.pop(request_id, None)
            self.execution_times.pop(request_id, None)
            self._deadlines.pop(request_id, None)
            self._metadata.pop(request_id, None)

    def extend_deadline(self, request_id: str, additional_seconds: float) -> Optional[Dict[str, Optional[str]]]:
        """
        Extend the deadline for a pending tool execution.
        Called when Android sends a tool_status indicating it needs more time (e.g., waiting for unlock).

        Args:
            request_id: The request ID to extend
            additional_seconds: Number of seconds to add to the deadline

        Returns:
            The stored metadata (tool_use_id, session_id) if found, None otherwise
        """
        if request_id not in self._deadlines:
            logger.warning(f"extend_deadline: No pending deadline for request_id={request_id} (may have already completed)")
            return None

        old_deadline = self._deadlines[request_id]
        new_deadline = time.monotonic() + additional_seconds
        self._deadlines[request_id] = new_deadline

        # Also update execution_times to prevent cleanup_old_executions from reaping this
        self.execution_times[request_id] = datetime.now()

        logger.info(f"Extended deadline for {request_id} by {additional_seconds}s (was {old_deadline - time.monotonic():.1f}s remaining, now {additional_seconds:.1f}s)")

        return self._metadata.get(request_id)

    def handle_tool_result(self, request_id: str, result: Dict[str, Any]) -> bool:
        """
        Handle an incoming tool result from the Android device.

        Args:
            request_id: The request ID this result corresponds to
            result: The tool execution result

        Returns:
            True if a pending execution was found and completed, False otherwise
        """
        future = self.pending_executions.get(request_id)

        if future and not future.done():
            # Complete the future with the result
            future.set_result(result)
            logger.info(f"Completed pending execution for request {request_id}")
            return True
        else:
            if future and future.done():
                logger.warning(f"Received duplicate result for request {request_id}")
            else:
                logger.warning(f"Received result for unknown request {request_id}")
            return False

    def cleanup_old_executions(self, max_age_seconds: int = 60):
        """
        Clean up old pending executions that were never completed.
        This prevents memory leaks from abandoned requests.

        Args:
            max_age_seconds: Maximum age for pending executions
        """
        now = datetime.now()
        cutoff_time = now - timedelta(seconds=max_age_seconds)

        to_remove = []
        for request_id, timestamp in self.execution_times.items():
            if timestamp < cutoff_time:
                to_remove.append(request_id)

        for request_id in to_remove:
            future = self.pending_executions.pop(request_id, None)
            self.execution_times.pop(request_id, None)
            self._deadlines.pop(request_id, None)
            self._metadata.pop(request_id, None)

            if future and not future.done():
                # Cancel the future with a timeout error
                future.set_exception(
                    asyncio.TimeoutError(f"Execution abandoned after {max_age_seconds} seconds")
                )
                logger.info(f"Cleaned up abandoned execution {request_id}")

    def get_pending_count(self) -> int:
        """Get the number of pending tool executions."""
        return len(self.pending_executions)

# Global instance
tool_result_handler = ToolResultHandler()
