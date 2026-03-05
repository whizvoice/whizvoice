"""
Screen Agent Queue - Queue manager for screen agent tools.

Ensures only one screen agent tool executes at a time per device.
When a tool is called while one is already running, it queues and returns immediately.
Results are delivered via WebSocket when execution completes.

Uses device_id as the queue key so that queue state persists across conversation switches
on the same device, and different devices have independent queues.
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

    Ensures only one screen agent tool executes at a time per device.
    When a tool is called while one is already running, it queues and returns
    immediately with status. Results are delivered via WebSocket.

    Uses device_id as the key to enable queue persistence across conversation
    switches on the same device.
    """

    def __init__(self):
        # Pending queued items per device
        self._queues: Dict[str, List[QueuedToolExecution]] = {}
        # Currently executing tool per device (queue_id)
        self._executing: Dict[str, str] = {}
        # Lock per device for thread safety
        self._locks: Dict[str, asyncio.Lock] = {}
        # Function to execute tools (set by app.py on startup)
        self._execute_tool_func: Optional[Callable] = None

    def set_execute_tool_func(self, func: Callable):
        """Set the function to use for executing tools."""
        self._execute_tool_func = func
        logger.info("Screen agent queue: execute_tool function registered")

    def _get_lock(self, device_id: str) -> asyncio.Lock:
        """Get or create a lock for a device. Thread-safe via setdefault."""
        return self._locks.setdefault(device_id, asyncio.Lock())

    def is_screen_agent_tool(self, tool_name: str) -> bool:
        """Check if a tool needs queuing (is a screen agent tool)."""
        return tool_name.startswith(SCREEN_AGENT_PREFIX)

    async def enqueue(
        self,
        device_id: str,
        tool_name: str,
        tool_args: Dict[str, Any],
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Queue a screen agent tool for execution.

        If no tool is currently executing for this device, executes immediately.
        Otherwise, queues the tool and returns a queued status.

        Args:
            device_id: The device ID (persists across conversations on same device)
            tool_name: Name of the tool to execute
            tool_args: Arguments for the tool
            context: Execution context (websocket, tool_result_handler, etc.)

        Returns:
            If executing immediately: the tool result
            If queued: {"status": "queued", "position": N, "queue_id": "..."}
        """
        queue_id = f"sq_{uuid.uuid4().hex[:12]}"
        lock = self._get_lock(device_id)

        async with lock:
            # Check if something is already executing
            if device_id in self._executing:
                # Queue the tool
                if device_id not in self._queues:
                    self._queues[device_id] = []

                queued_item = QueuedToolExecution(
                    queue_id=queue_id,
                    tool_name=tool_name,
                    tool_args=tool_args,
                    context=context
                )
                self._queues[device_id].append(queued_item)
                position = len(self._queues[device_id])

                logger.info(
                    f"Screen agent queue: Queued {tool_name} for device {device_id}, "
                    f"position={position}, queue_id={queue_id}"
                )

                return "Result pending..."

            # Nothing executing - mark as executing and run
            self._executing[device_id] = queue_id
            logger.info(
                f"Screen agent queue: Executing {tool_name} immediately for device {device_id}, "
                f"queue_id={queue_id}"
            )

        # Execute outside the lock
        result = await self._execute_and_process_queue(device_id, tool_name, tool_args, context)
        return result

    async def _refresh_websocket_if_stale(self, context: dict):
        """Replace stale WebSocket in context with active one from existing registries."""
        websocket = context.get("websocket")
        if not websocket:
            return

        from starlette.websockets import WebSocketState
        if websocket.client_state == WebSocketState.CONNECTED:
            return  # Still good

        session_id = context.get("session_id")
        conversation_id = context.get("conversation_id")
        logger.warning(
            f"Screen agent queue: WebSocket is {websocket.client_state}, "
            f"looking up active connection (session={session_id}, conversation={conversation_id})"
        )

        # Strategy 1: Resolve session redirect and look up new WebSocket
        if session_id:
            try:
                from redis_managers import chat_session_manager, local_manager
                resolved_id = await chat_session_manager.resolve_session_id(session_id)
                if resolved_id != session_id:
                    fresh_ws = await local_manager.get_session_websocket(resolved_id)
                    if fresh_ws and fresh_ws.client_state == WebSocketState.CONNECTED:
                        context["websocket"] = fresh_ws
                        logger.info(f"Refreshed WebSocket via session redirect: {session_id} -> {resolved_id}")
                        return
            except Exception as e:
                logger.warning(f"Error resolving session redirect: {e}")

        # Strategy 2: Look up by conversation_id
        if conversation_id is not None:
            try:
                from redis_managers import local_manager
                real_id = conversation_id
                if isinstance(conversation_id, int) and conversation_id < 0:
                    cached_real = await local_manager.get_real_id_cached(conversation_id)
                    if cached_real:
                        real_id = cached_real
                registrations = await local_manager.get_conversation_websockets(real_id)
                for sid, ws in registrations:
                    if ws.client_state == WebSocketState.CONNECTED:
                        context["websocket"] = ws
                        logger.info(f"Refreshed WebSocket via conversation lookup (conv_id={real_id}, session={sid})")
                        return
            except Exception as e:
                logger.warning(f"Error looking up WebSocket by conversation: {e}")

        logger.warning(f"Could not find active WebSocket to replace stale one")

    async def _execute_and_process_queue(
        self,
        device_id: str,
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
            # Refresh WebSocket if the original one disconnected (e.g., during optimistic→real ID migration)
            await self._refresh_websocket_if_stale(context)
            # Execute the current tool
            # Note: user_id is already in context, passed via **context
            result = await self._execute_tool_func(
                tool_name,
                tool_args,
                **context
            )
            return result
        finally:
            # Process the queue
            await self._process_next_in_queue(device_id)

    async def _process_next_in_queue(self, device_id: str):
        """Process the next item in the queue for a device."""
        lock = self._get_lock(device_id)

        async with lock:
            # Clear executing flag
            if device_id in self._executing:
                del self._executing[device_id]

            # Check if there's anything queued
            if device_id not in self._queues or not self._queues[device_id]:
                logger.debug(f"Screen agent queue: No more items in queue for device {device_id}")
                return

            # Pop the next item
            next_item = self._queues[device_id].pop(0)

            # Clean up empty queue
            if not self._queues[device_id]:
                del self._queues[device_id]

            # Mark as executing
            self._executing[device_id] = next_item.queue_id

            logger.info(
                f"Screen agent queue: Processing queued {next_item.tool_name} for device {device_id}, "
                f"queue_id={next_item.queue_id}"
            )

        # Execute outside the lock
        try:
            if not self._execute_tool_func:
                logger.error("Screen agent queue: execute_tool function not set!")
                result = {"error": "Queue manager not initialized", "success": False}
            else:
                # Refresh WebSocket if the original one disconnected
                await self._refresh_websocket_if_stale(next_item.context)
                # Note: user_id is already in context, passed via **context
                result = await self._execute_tool_func(
                    next_item.tool_name,
                    next_item.tool_args,
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
            await self._process_next_in_queue(device_id)

    async def _send_queued_result(self, item: QueuedToolExecution, result: Dict[str, Any]):
        """Send the result of a queued tool execution via WebSocket."""
        await self._refresh_websocket_if_stale(item.context)
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

    async def cancel_pending(self, device_id: str) -> Dict[str, Any]:
        """
        Cancel all pending (queued, not executing) screen agent tools for a device.

        The currently executing tool is NOT cancelled.

        Returns:
            Dict with cancelled_count and list of cancelled tools
        """
        lock = self._get_lock(device_id)

        async with lock:
            if device_id not in self._queues or not self._queues[device_id]:
                return {
                    "status": "success",
                    "cancelled_count": 0,
                    "cancelled_tools": [],
                    "message": "No pending tools to cancel"
                }

            # Get all queued items
            cancelled_items = self._queues[device_id]
            cancelled_count = len(cancelled_items)
            cancelled_tools = [item.to_dict() for item in cancelled_items]

            # Clear the queue
            del self._queues[device_id]

            logger.info(
                f"Screen agent queue: Cancelled {cancelled_count} pending tools for device {device_id}"
            )

            return {
                "status": "success",
                "cancelled_count": cancelled_count,
                "cancelled_tools": cancelled_tools,
                "message": f"Cancelled {cancelled_count} pending tool(s)"
            }

    async def cleanup_device(self, device_id: str):
        """Clean up queue state for a device. Only called for explicit cleanup, not on session disconnect."""
        lock = self._get_lock(device_id)

        async with lock:
            # Clear queue
            if device_id in self._queues:
                queue_size = len(self._queues[device_id])
                del self._queues[device_id]
                logger.info(f"Screen agent queue: Cleaned up {queue_size} queued items for device {device_id}")

            # Clear executing flag
            if device_id in self._executing:
                del self._executing[device_id]
                logger.info(f"Screen agent queue: Cleared executing flag for device {device_id}")

            # Clean up lock
            if device_id in self._locks:
                del self._locks[device_id]

    def get_queue_status(self, device_id: str) -> Dict[str, Any]:
        """Get the current queue status for a device."""
        is_executing = device_id in self._executing
        queue_length = len(self._queues.get(device_id, []))

        return {
            "device_id": device_id,
            "is_executing": is_executing,
            "queue_length": queue_length,
            "queued_tools": [
                item.to_dict() for item in self._queues.get(device_id, [])
            ]
        }


# Global instance
screen_agent_queue = ScreenAgentQueueManager()
