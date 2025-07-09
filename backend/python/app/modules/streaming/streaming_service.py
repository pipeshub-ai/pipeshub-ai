import json
import asyncio
from typing import Any, Callable, Dict, Optional, AsyncGenerator


class StreamingService:
    def __init__(self, event_callback: Optional[Callable[[str], None]] = None) -> None:
        self.event_callback = event_callback

    def send_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Send an SSE event directly via callback"""
        event = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
        if self.event_callback:
            self.event_callback(event)
        else:
            # Fallback for non-streaming mode
            print(f"Sending event: {event_type} with data: {data}")