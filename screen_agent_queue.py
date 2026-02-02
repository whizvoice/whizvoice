"""
Screen Agent Queue - Queue manager for screen agent tools.

Ensures only one screen agent tool executes at a time per session.
When a tool is called while one is already running, it queues and returns immediately.
Results are delivered via WebSocket when execution completes.
"""

import asyncio
import logging
import uuid
import json
from typing import Dict, List, Any, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)

# Prefix for screen agent tools that need queuing (operate on device screen)
SCREEN_AGENT_PREFIX = "agent_"


@dataclass
class QueuedToolExecution:
    """Represents a queued tool execution."""
    queue_id: str
    tool_name: str
    tool_args: Dict[str, Any]
    context: Dict[str, Any]
    created_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for logging/serialization."""
        return {
            "queue_id": self.queue_id,
            "tool_name": self.tool_name,
            "created_at": self.created_at.isoformat(),
        }


class ScreenAgentQueueManager:
    """
    Manages queuing for screen agent tools.

    Ensures only one screen agent tool executes at a time per session.
    When a tool is called while one is already running, it queues and returns
    immediately with status. Results are delivered via WebSocket.
    """

    def __init__(self):
        # Pending queued items per session
        self._queues: Dict[str, List[QueuedToolExecution]] = {}
        # Currently executing tool per session (queue_id)
        self._executing: Dict[str, str] = {}
        # Lock per session for thread safety
        self._locks: Dict[str, asyncio.Lock] = {}
        # Function to execute tools (set by app.py on startup)
        self._execute_tool_func: Optional[Callable] = None

    def set_execute_tool_func(self, func: Callable):
        """Set the function to use for executing tools."""
        self._execute_tool_func = func
        logger.info("Screen agent queue: execute_tool function registered")

    def _get_lock(self, session_id: str) -> asyncio.Lock:
        """Get or create a lock for a session. Thread-safe via setdefault."""
        return self._locks.setdefault(session_id, asyncio.Lock())

    def is_screen_agent_tool(self, tool_name: str) -> bool:
        """Check if a tool needs queuing (is a screen agent tool)."""
        return tool_name.startswith(SCREEN_AGENT_PREFIX)

    async def enqueue(
        self,
        session_id: str,
        tool_name: str,
        tool_args: Dict[str, Any],
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Queue a screen agent tool for execution.

        If no tool is currently executing for this session, executes immediately.
        Otherwise, queues the tool and returns a queued status.

        Args:
            session_id: The session ID
            tool_name: Name of the tool to execute
            tool_args: Arguments for the tool
            context: Execution context (websocket, tool_result_handler, etc.)

        Returns:
            If executing immediately: the tool result
            If queued: {"status": "queued", "position": N, "queue_id": "..."}
        """
        queue_id = f"sq_{uuid.uuid4().hex[:12]}"
        lock = self._get_lock(session_id)

        async with lock:
            # Check if something is already executing
            if session_id in self._executing:
                # Queue the tool
                if session_id not in self._queues:
                    self._queues[session_id] = []

                queued_item = QueuedToolExecution(
                    queue_id=queue_id,
                    tool_name=tool_name,
                    tool_args=tool_args,
                    context=context
                )
                self._queues[session_id].append(queued_item)
                position = len(self._queues[session_id])

                logger.info(
                    f"Screen agent queue: Queued {tool_name} for session {session_id}, "
                    f"position={position}, queue_id={queue_id}"
                )

                return {
                    "status": "queued",
                    "position": position,
                    "queue_id": queue_id,
                    "tool_name": tool_name,
                    "message": f"Tool {tool_name} queued at position {position}. "
                               f"Will execute after current operation completes."
                }

            # Nothing executing - mark as executing and run
            self._executing[session_id] = queue_id
            logger.info(
                f"Screen agent queue: Executing {tool_name} immediately for session {session_id}, "
                f"queue_id={queue_id}"
            )

        # Execute outside the lock
        result = await self._execute_and_process_queue(session_id, tool_name, tool_args, context)
        return result

    async def _execute_and_process_queue(
        self,
        session_id: str,
        tool_name: str,
        tool_args: Dict[str, Any],
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Execute a tool and then process any queued items.

        This runs the tool, then checks if there are queued items and executes them.
        Queued item results are sent via WebSocket.
        """
        if not self._execute_tool_func:
            logger.error("Screen agent queue: execute_tool function not set!")
            return {"error": "Queue manager not initialized", "success": False}

        try:
            # Execute the current tool
            user_id = context.get("user_id")
            result = await self._execute_tool_func(
                tool_name,
                tool_args,
                user_id,
                **context
            )
            return result
        finally:
            # Process the queue
            await self._process_next_in_queue(session_id)

    async def _process_next_in_queue(self, session_id: str):
        """Process the next item in the queue for a session."""
        lock = self._get_lock(session_id)

        async with lock:
            # Clear executing flag
            if session_id in self._executing:
                del self._executing[session_id]

            # Check if there's anything queued
            if session_id not in self._queues or not self._queues[session_id]:
                logger.debug(f"Screen agent queue: No more items in queue for session {session_id}")
                return

            # Pop the next item
            next_item = self._queues[session_id].pop(0)

            # Clean up empty queue
            if not self._queues[session_id]:
                del self._queues[session_id]

            # Mark as executing
            self._executing[session_id] = next_item.queue_id

            logger.info(
                f"Screen agent queue: Processing queued {next_item.tool_name} for session {session_id}, "
                f"queue_id={next_item.queue_id}"
            )

        # Execute outside the lock
        try:
            if not self._execute_tool_func:
                logger.error("Screen agent queue: execute_tool function not set!")
                result = {"error": "Queue manager not initialized", "success": False}
            else:
                user_id = next_item.context.get("user_id")
                result = await self._execute_tool_func(
                    next_item.tool_name,
                    next_item.tool_args,
                    user_id,
                    **next_item.context
                )

            # Send result via WebSocket
            await self._send_queued_result(next_item, result)

        except Exception as e:
            logger.error(f"Screen agent queue: Error executing queued tool: {e}")
            result = {"error": str(e), "success": False}
            await self._send_queued_result(next_item, result)

        finally:
            # Process next item in queue
            await self._process_next_in_queue(session_id)

    async def _send_queued_result(self, item: QueuedToolExecution, result: Dict[str, Any]):
        """Send the result of a queued tool execution via WebSocket."""
        websocket = item.context.get("websocket")
        if not websocket:
            logger.warning(f"Screen agent queue: No websocket for queued result delivery")
            return

        try:
            message = {
                "type": "queued_tool_result",
                "queue_id": item.queue_id,
                "tool_name": item.tool_name,
                "result": result,
                "conversation_id": item.context.get("conversation_id")
            }
            await websocket.send_text(json.dumps(message))
            logger.info(f"Screen agent queue: Sent queued result for {item.tool_name}, queue_id={item.queue_id}")
        except Exception as e:
            logger.error(f"Screen agent queue: Failed to send queued result: {e}")

    async def cancel_pending(self, session_id: str) -> Dict[str, Any]:
        """
        Cancel all pending (queued, not executing) screen agent tools for a session.

        The currently executing tool is NOT cancelled.

        Returns:
            Dict with cancelled_count and list of cancelled tools
        """
        lock = self._get_lock(session_id)

        async with lock:
            if session_id not in self._queues or not self._queues[session_id]:
                return {
                    "status": "success",
                    "cancelled_count": 0,
                    "cancelled_tools": [],
                    "message": "No pending tools to cancel"
                }

            # Get all queued items
            cancelled_items = self._queues[session_id]
            cancelled_count = len(cancelled_items)
            cancelled_tools = [item.to_dict() for item in cancelled_items]

            # Clear the queue
            del self._queues[session_id]

            logger.info(
                f"Screen agent queue: Cancelled {cancelled_count} pending tools for session {session_id}"
            )

            return {
                "status": "success",
                "cancelled_count": cancelled_count,
                "cancelled_tools": cancelled_tools,
                "message": f"Cancelled {cancelled_count} pending tool(s)"
            }

    async def cleanup_session(self, session_id: str):
        """Clean up queue state when a session ends."""
        lock = self._get_lock(session_id)

        async with lock:
            # Clear queue
            if session_id in self._queues:
                queue_size = len(self._queues[session_id])
                del self._queues[session_id]
                logger.info(f"Screen agent queue: Cleaned up {queue_size} queued items for session {session_id}")

            # Clear executing flag
            if session_id in self._executing:
                del self._executing[session_id]
                logger.info(f"Screen agent queue: Cleared executing flag for session {session_id}")

            # Clean up lock
            if session_id in self._locks:
                del self._locks[session_id]

    def get_queue_status(self, session_id: str) -> Dict[str, Any]:
        """Get the current queue status for a session."""
        is_executing = session_id in self._executing
        queue_length = len(self._queues.get(session_id, []))

        return {
            "session_id": session_id,
            "is_executing": is_executing,
            "queue_length": queue_length,
            "queued_tools": [
                item.to_dict() for item in self._queues.get(session_id, [])
            ]
        }


# Global instance
screen_agent_queue = ScreenAgentQueueManager()
