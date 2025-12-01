"""
⚡ STREAMING OPTIMIZER ⚡
Improves perceived performance through intelligent streaming
"""

import asyncio
from typing import Any


class StreamingOptimizer:
    """Optimizes response streaming for better UX"""

    def __init__(self):
        self.chunk_size = 50  # chars per chunk
        self.min_delay_ms = 10  # minimum delay between chunks
        self.max_delay_ms = 30  # maximum delay between chunks

    async def stream_response(
        self,
        content: str,
        writer: Any,
        event_type: str = "chunk"
    ) -> None:
        """
        Stream response in optimized chunks

        Args:
            content: Full response content
            writer: Stream writer function
            event_type: Event type for streaming
        """

        if not content:
            return

        # Split into chunks
        chunks = self._split_into_chunks(content)

        for i, chunk in enumerate(chunks):
            # Calculate adaptive delay based on chunk position
            delay = self._calculate_delay(i, len(chunks))

            # Write chunk
            writer({
                "event": event_type,
                "data": {
                    "chunk": chunk,
                    "index": i,
                    "total": len(chunks)
                }
            })

            # Delay for streaming effect (non-blocking)
            if i < len(chunks) - 1:  # Don't delay after last chunk
                await asyncio.sleep(delay / 1000)  # Convert ms to seconds

    def _split_into_chunks(self, content: str) -> list[str]:
        """
        Split content into streaming chunks

        Strategy:
        - Split by sentences when possible
        - Fall back to word boundaries
        - Preserve markdown formatting
        """

        chunks = []
        current_chunk = ""

        # Split by sentences first
        sentences = content.split('. ')

        for i, sentence in enumerate(sentences):
            # Add period back (except for last sentence)
            if i < len(sentences) - 1:
                sentence += '. '

            # Check if adding this sentence exceeds chunk size
            if len(current_chunk) + len(sentence) > self.chunk_size:
                # Flush current chunk if not empty
                if current_chunk:
                    chunks.append(current_chunk)
                    current_chunk = ""

                # If sentence itself is too long, split by words
                if len(sentence) > self.chunk_size:
                    words = sentence.split(' ')
                    for word in words:
                        if len(current_chunk) + len(word) + 1 > self.chunk_size:
                            if current_chunk:
                                chunks.append(current_chunk)
                            current_chunk = word + ' '
                        else:
                            current_chunk += word + ' '
                else:
                    current_chunk = sentence
            else:
                current_chunk += sentence

        # Add final chunk
        if current_chunk:
            chunks.append(current_chunk)

        return chunks if chunks else [content]

    def _calculate_delay(self, chunk_index: int, total_chunks: int) -> float:
        """
        Calculate optimal delay for streaming chunk

        Strategy:
        - Start fast (low delay) to show immediate response
        - Slow down slightly in middle for readability
        - Speed up at end to finish quickly
        """

        if total_chunks <= 1:
            return 0

        # Normalize position (0.0 to 1.0)
        position = chunk_index / (total_chunks - 1)

        # U-shaped curve: fast at start and end, slower in middle
        # delay = min_delay + (max_delay - min_delay) * (1 - |2*position - 1|)

        if position < 0.3:
            # Fast start
            return self.min_delay_ms
        elif position > 0.8:
            # Fast finish
            return self.min_delay_ms
        else:
            # Slower middle (for readability)
            return self.max_delay_ms * 0.6

    async def stream_llm_thinking(
        self,
        writer: Any,
        message: str = "Thinking..."
    ) -> None:
        """
        Stream thinking indicator while waiting for LLM

        Shows progress to user while LLM processes
        """

        thinking_messages = [
            "🤔 Analyzing your request...",
            "🔍 Searching for information...",
            "⚙️ Processing data...",
            "✨ Preparing response..."
        ]

        # Rotate through thinking messages
        for i, msg in enumerate(thinking_messages):
            writer({
                "event": "thinking",
                "data": {
                    "message": msg,
                    "progress": int((i + 1) / len(thinking_messages) * 100)
                }
            })

            await asyncio.sleep(0.5)  # 500ms between updates

    def estimate_streaming_time(self, content: str) -> float:
        """
        Estimate total streaming time in milliseconds

        Returns:
            Estimated streaming duration in ms
        """
        chunks = self._split_into_chunks(content)
        total_delay = sum(
            self._calculate_delay(i, len(chunks))
            for i in range(len(chunks))
        )
        return total_delay

    def optimize_for_length(self, content_length: int) -> None:
        """
        Adjust streaming parameters based on content length

        Args:
            content_length: Length of content in characters
        """

        if content_length < 200:
            # Short content: larger chunks, faster streaming
            self.chunk_size = 100
            self.min_delay_ms = 5
            self.max_delay_ms = 15
        elif content_length < 1000:
            # Medium content: balanced streaming
            self.chunk_size = 50
            self.min_delay_ms = 10
            self.max_delay_ms = 30
        else:
            # Long content: smaller chunks, maintain engagement
            self.chunk_size = 30
            self.min_delay_ms = 15
            self.max_delay_ms = 40


# Global instance
_streaming_optimizer = StreamingOptimizer()


def get_streaming_optimizer() -> StreamingOptimizer:
    """Get global streaming optimizer instance"""
    return _streaming_optimizer

