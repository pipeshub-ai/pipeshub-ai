import logging
import re
from typing import Any
from urllib.parse import urlparse

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

from app.utils.html_to_blocks import html_to_blocks
from app.utils.url_fetcher import fetch_url

logger = logging.getLogger(__name__)

HTTP_STATUS_OK = 200


class FetchUrlArgs(BaseModel):
    """Arguments for fetch URL tool."""
    url: str = Field(
        ...,
        description="The URL to fetch content from. Must be a valid HTTP/HTTPS URL."
    )

def split_long_text(text: str, max_words: int = 200) -> list[str]:
    """
    Split text into chunks at sentence boundaries, respecting max_words limit.
    """
    if not text:
        return []

    words = text.split()
    if len(words) <= max_words:
        return [text]

    # Split at sentence boundaries (., !, ?)
    sentences = re.split(r'([.!?]+\s+)', text)

    chunks = []
    current_chunk = []
    current_word_count = 0

    for i in range(0, len(sentences), 2):
        sentence = sentences[i]
        # Add punctuation back if it exists
        if i + 1 < len(sentences):
            sentence += sentences[i + 1]

        sentence_words = sentence.split()
        sentence_word_count = len(sentence_words)

        if current_word_count + sentence_word_count > max_words and current_chunk:
            # Current chunk is full, save it
            chunks.append(''.join(current_chunk).strip())
            current_chunk = [sentence]
            current_word_count = sentence_word_count
        else:
            current_chunk.append(sentence)
            current_word_count += sentence_word_count

    # Add remaining chunk
    if current_chunk:
        chunks.append(''.join(current_chunk).strip())

    # If no sentence boundaries found, fall back to word-based splitting
    if not chunks or (len(chunks) == 1 and len(chunks[0].split()) > max_words):
        chunks = []
        for i in range(0, len(words), max_words):
            chunk_words = words[i:i + max_words]
            chunks.append(' '.join(chunk_words))

    return chunks





def create_fetch_url_tool(
    url_counter: dict[str, int] | None = None,
    is_multimodal_llm: bool = False,
) -> BaseTool:
    """
    Factory function to create fetch URL tool.

    Args:
        url_counter: Shared counter dict to track URL numbers across multiple calls.
                    Pass {"count": 0} to track W1, W2, W3, etc.
    """
    if url_counter is None:
        url_counter = {"count": 0}

    @tool("fetch_url", args_schema=FetchUrlArgs)
    def fetch_url_tool(url: str) -> dict[str, Any]:
        """
        This tool Fetches and extracts main content from a URL for detailed analysis.

        Use this tool when you need the full content from a specific webpage to answer the query accurately. If multiple URLs are available, select the ones most likely to contain the required information and invoke the tool separately for each selected URL.

        The content is returned as array of blocks.

        Args:
            url: The URL to fetch content from (must be HTTP/HTTPS)

        Example:
            fetch_url(url="https://docs.python.org/3/tutorial/classes.html")
        """
        try:
            parsed = urlparse(url)
            if parsed.scheme not in ('http', 'https'):
                return {
                    "ok": False,
                    "error": f"Invalid URL scheme: {parsed.scheme}. Only HTTP/HTTPS supported."
                }

            if not parsed.netloc:
                return {
                    "ok": False,
                    "error": "Invalid URL: no domain specified"
                }

            response = fetch_url(url,verbose=True)

            if response.status_code != HTTP_STATUS_OK:
                return {
                    "ok": False,
                    "error": f"{response.text}, status: {response.status_code}"
                }

            html_content = response.text


            blocks = html_to_blocks(
                html_content,
                use_trafilatura=False,
                base_url=f"{parsed.scheme}://{parsed.netloc}",
                is_multimodal_llm=is_multimodal_llm,
            )

            if not blocks:
                return {
                    "ok": False,
                    "error": "No content available from the url"
                }

            url_counter["count"] += 1
            url_number = url_counter["count"]

            logger.info(f"Fetched URL {url}: {len(blocks)} blocks extracted, assigned W{url_number}")

            return {
                "ok": True,
                "result_type": "url_content",
                "url": url,
                "blocks": blocks,
                "url_number": url_number
            }
        except Exception as e:
            logger.exception("Unexpected error fetching URL %s: %s", url, str(e))
            return {
                "ok": False,
                "error": str(e)
            }

    return fetch_url_tool
