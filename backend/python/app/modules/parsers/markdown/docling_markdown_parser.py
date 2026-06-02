"""Docling-backed Markdown parser.

Converts Markdown → HTML (via python-markdown), then feeds the HTML bytes into
Docling's ``DocumentConverter``.  Use this parser when you need Docling's
layout-analysis output (bounding boxes, page numbers, richer table detection)
for Markdown content that has been stored as a file or must round-trip through
the Docling pipeline.

For a faster, purely structural parse that produces ``BlocksContainer`` directly
without ML overhead, use :class:`MarkdownItParser` instead.
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

import markdown as markdown_lib
from bs4 import BeautifulSoup
from docling.datamodel.document import DoclingDocument
from docling.document_converter import DocumentConverter


class DoclingMarkdownParser:
    """Markdown parser backed by Docling.

    Responsibilities
    ----------------
    * ``parse_string`` – convert a Markdown string to HTML bytes ready for
      Docling ingestion.
    * ``parse_file`` – parse a ``.md`` file on disk via Docling's
      ``DocumentConverter`` and return a ``DoclingDocument``.
    * ``extract_and_replace_images`` – pre-process image references so that
      alt-text labels are normalised to ``Image_N`` before parsing.  This is
      needed both by this parser and the markdownit parser, so callers can use
      whichever parser they have in hand.
    """

    def __init__(self) -> None:
        self.converter = DocumentConverter()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse_string(self, md_content: str) -> bytes:
        """Convert Markdown to HTML bytes for Docling ingestion.

        The returned bytes should be passed to
        ``DoclingProcessor.parse_document`` as the file content.

        Args:
            md_content: Markdown source string.

        Returns:
            UTF-8 encoded HTML.
        """
        html = markdown_lib.markdown(md_content, extensions=["md_in_html"])
        return html.encode("utf-8")

    def parse_file(self, file_path: str) -> DoclingDocument:
        """Parse a Markdown file via Docling.

        Args:
            file_path: Absolute or relative path to the ``.md`` file.

        Returns:
            Parsed ``DoclingDocument``.

        Raises:
            ValueError: If Docling reports a non-success status.
        """
        result = self.converter.convert(file_path)
        if result.status.value != "success":
            raise ValueError(f"Failed to parse Markdown: {result.status}")
        return result.document

    def extract_and_replace_images(
        self, md_content: str
    ) -> Tuple[str, List[Dict[str, str]]]:
        """Extract images and replace alt-text with sequential ``Image_N`` labels.

        Handles inline Markdown images, reference-style images, and ``<img>``
        HTML tags embedded in the Markdown.

        Args:
            md_content: Raw Markdown source.

        Returns:
            A 2-tuple of:
            - Modified Markdown with normalised alt-text.
            - List of dicts describing each image::

                {
                    'original_text': str,
                    'url': str,
                    'alt_text': str,      # original alt text
                    'new_alt_text': str,  # Image_N
                    'image_type': str,    # 'markdown' | 'reference' | 'html'
                }
        """
        return _extract_and_replace_images(md_content)


# ---------------------------------------------------------------------------
# Shared implementation (used by both parser classes in this package)
# ---------------------------------------------------------------------------

def _extract_and_replace_images(
    md_content: str,
) -> Tuple[str, List[Dict[str, str]]]:
    """Module-level implementation shared by both markdown parser backends."""
    images: List[Dict[str, str]] = []
    image_counter = [1]

    markdown_img_pattern = r'!\[([^\]]*)\]\(([^\s)]+)(?:\s+"[^"]*")?\)'
    reference_usage_pattern = r'!\[([^\]]*)\]\[([^\]]+)\]'
    reference_def_pattern = r'^\[([^\]]+)\]:\s+([^\s]+)(?:\s+"[^"]*")?\s*$'

    reference_map: Dict[str, str] = {}
    for match in re.finditer(reference_def_pattern, md_content, re.MULTILINE):
        reference_map[match.group(1)] = match.group(2).strip()

    reference_positions: set[int] = set()
    for match in re.finditer(reference_usage_pattern, md_content):
        reference_positions.add(match.start())

    def replace_reference_image(match: re.Match) -> str:  # type: ignore[type-arg]
        original_alt = match.group(1)
        ref_id = match.group(2)
        url = reference_map.get(ref_id, f"[unknown reference: {ref_id}]")
        new_alt = f"Image_{image_counter[0]}"
        images.append({
            "original_text": match.group(0),
            "url": url,
            "alt_text": original_alt,
            "new_alt_text": new_alt,
            "image_type": "reference",
        })
        image_counter[0] += 1
        return f"![{new_alt}][{ref_id}]"

    def replace_markdown_image(match: re.Match) -> str:  # type: ignore[type-arg]
        if match.start() in reference_positions:
            return match.group(0)
        original_alt = match.group(1)
        url = match.group(2)
        new_alt = f"Image_{image_counter[0]}"
        images.append({
            "original_text": match.group(0),
            "url": url,
            "alt_text": original_alt,
            "new_alt_text": new_alt,
            "image_type": "markdown",
        })
        image_counter[0] += 1
        return f"![{new_alt}]({url})"

    def process_html_images(content: str) -> str:
        soup = BeautifulSoup(content, "html.parser")
        for img_tag in soup.find_all("img"):
            src = img_tag.get("src", "")
            original_alt = img_tag.get("alt", "")
            original_text = str(img_tag)
            new_alt = f"Image_{image_counter[0]}"
            img_tag["alt"] = new_alt
            images.append({
                "original_text": original_text,
                "url": src,
                "alt_text": original_alt,
                "new_alt_text": new_alt,
                "image_type": "html",
            })
            image_counter[0] += 1
        return str(soup)

    modified = re.sub(reference_usage_pattern, replace_reference_image, md_content)
    modified = re.sub(markdown_img_pattern, replace_markdown_image, modified)
    modified = process_html_images(modified)
    return modified, images
