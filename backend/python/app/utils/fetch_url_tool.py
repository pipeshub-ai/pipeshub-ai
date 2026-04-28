import dataclasses
import json
import logging
import re
from typing import Any
from urllib.parse import urlparse

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

from app.utils.chat_helpers import CitationRefMapper
from app.utils.citations import extract_tiny_ref
from app.utils.html_to_blocks import html_to_blocks
from app.utils.url_fetcher import fetch_url

logger = logging.getLogger(__name__)

HTTP_STATUS_OK = 200
_MAX_ERROR_PREVIEW_CHARS = 500


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





def _resolve_tiny_ref_url(url: str, ref_mapper: CitationRefMapper | None) -> str:
    """If the incoming URL is a tiny web-ref (https://refN.xyz), resolve it to the
    real URL stored in the ref_mapper. Otherwise return the URL unchanged.

    Also strips any text-fragment (#:~:text=...) before fetching so the HTTP
    request targets the page itself, not the fragment.
    """
    if not url:
        return url
    inner_ref = extract_tiny_ref(url)
    if inner_ref and ref_mapper is not None:
        resolved = ref_mapper.ref_to_url.get(inner_ref)
        if resolved:
            url = resolved
    # Drop text fragment: HTTP servers do not use it, and keeping it breaks fetches for some hosts.
    if "#:~:text=" in url:
        url = url.split("#:~:text=", 1)[0]
    return url


def create_fetch_url_tool(
    ref_mapper: CitationRefMapper | None = None,
) -> BaseTool:
    """
    Factory function to create fetch URL tool.

    Args:
        ref_mapper: Shared CitationRefMapper. When the LLM passes a tiny web-ref URL
                    (https://refN.xyz) as the `url` argument, it is resolved back to
                    the real URL via this mapper.
    """
    @tool("fetch_url", args_schema=FetchUrlArgs)
    def fetch_url_tool(url: str) -> str:
        """
        This tool Fetches and extracts main content from a URL for detailed analysis.

        Use this tool when you need the full content from a specific webpage to answer the query accurately. If multiple URLs are available, select the ones most likely to contain the required information and invoke the tool separately for each selected URL.

        - **If tool fails for a URL**: check whether the context gathered so far is sufficient. If not, try other relevant URLs from the context before answering.
        Args:
            url: The URL to fetch content from (must be HTTP/HTTPS)

        Example:
            fetch_url(url="https://docs.python.org/3/tutorial/classes.html")
        """
        _URL_FALLBACK_HINT = (
            " If other relevant URLs are available and the current context is not "
            "sufficient to answer the query, fetch those URLs next."
        )
        try:
            url = _resolve_tiny_ref_url(url, ref_mapper)
            if "ref" in url and "xyz" in url:
                logger.warning(f"failed to resolve tiny ref url: {url}")
                return json.dumps({
                    "ok": False,
                    "error": "Failed to get content from that url, please try again with the correct URL, or" + _URL_FALLBACK_HINT
                })
            parsed = urlparse(url)
            if parsed.scheme not in ('http', 'https'):
                return json.dumps({
                    "ok": False,
                    "error": (
                        f"Invalid URL scheme: {parsed.scheme}. Only HTTP/HTTPS supported."
                        + _URL_FALLBACK_HINT
                    ),
                })

            if not parsed.netloc:
                return json.dumps({
                    "ok": False,
                    "error": "Invalid URL: no domain specified." + _URL_FALLBACK_HINT,
                })

            response = fetch_url(url, verbose=True)

            if response.status_code != HTTP_STATUS_OK:
                preview = (response.text or "")[:_MAX_ERROR_PREVIEW_CHARS]
                return json.dumps({
                    "ok": False,
                    "error": (
                        f"HTTP {response.status_code} error fetching {url}: {preview}."
                        + _URL_FALLBACK_HINT
                    ),
                })

            html_content = response.text

            blocks = html_to_blocks(
                html_content,
                use_trafilatura=False,
                base_url=f"{parsed.scheme}://{parsed.netloc}",
            )

            if not blocks:
                return json.dumps({
                    "ok": False,
                    "error": (
                        f"No readable content could be extracted from {url}."
                        + _URL_FALLBACK_HINT
                    ),
                })

            logger.info(f"Fetched URL {url}: {len(blocks)} blocks extracted")

            return json.dumps({
                "ok": True,
                "result_type": "url_content",
                "url": url,
                "blocks": [dataclasses.asdict(b) for b in blocks],
            })
        except Exception as e:
            logger.exception("Unexpected error fetching URL %s: %s", url, str(e))
            return json.dumps({
                "ok": False,
                "error": str(e) + _URL_FALLBACK_HINT,
            })

    return fetch_url_tool