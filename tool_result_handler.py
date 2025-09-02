"""
Handler for tracking pending tool executions and their results.
Uses in-memory storage with asyncio Futures for synchronous tool execution.
"""
import asyncio
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class ToolResultHandler:
    """Manages pending tool executions and their results."""
    
    def __init__(self):
        # Maps request_id to asyncio.Future
        self.pending_executions: Dict[str, asyncio.Future] = {}
        # Track creation time for cleanup
        self.execution_times: Dict[str, datetime] = {}
        
    async def wait_for_tool_result(self, request_id: str, timeout: float = 10.0) -> Dict[str, Any]:
        """
        Wait for a tool execution result with timeout.
        
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
        
        try:
            # Wait for the result with timeout
            result = await asyncio.wait_for(future, timeout=timeout)
            logger.info(f"Received tool result for request {request_id}: {result}")
            return result
            
        except asyncio.TimeoutError:
            logger.warning(f"Timeout waiting for tool result (request_id: {request_id})")
            return {
                "status": "timeout",
                "error": "Device did not respond within timeout period",
                "timeout_seconds": timeout
            }
            
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