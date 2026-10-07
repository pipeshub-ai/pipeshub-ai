"""Finding the images a Markdown document refers to.

Shared by both Markdown parser backends. In a module of its own, without the
Docling imports of ``docling_markdown_parser``, so a parse worker process can
run it on a large document without loading Docling.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

# CommonMark's grammar for an HTML open tag, so an <img> is rewritten exactly when
# markdown-it would treat it as a tag. Quoted values are single units, so a ">"
# inside alt text does not end the tag, and an unclosed "<img" followed by prose
# does not match at all.
_HTML_IMG_TAG_RE = re.compile(
    r"""<img"""
    r"""(?:\s+[A-Za-z_:][A-Za-z0-9_.:-]*"""
    r"""(?:\s*=\s*(?:[^\s"'=<>`]+|'[^']*'|"[^"]*"))?)*"""
    r"""\s*/?>""",
    re.IGNORECASE,
)


def extract_and_replace_images(
    md_content: str,
) -> tuple[str, list[dict[str, str]]]:
    """Extract images and replace their alt-text with sequential ``Image_N`` labels."""
    images: list[dict[str, str]] = []
    image_counter = 1

    markdown_img_pattern = r'!\[([^\]]*)\]\(([^\s)]+)(?:\s+"[^"]*")?\)'
    reference_usage_pattern = r'!\[([^\]]*)\]\[([^\]]+)\]'
    reference_def_pattern = r'^\[([^\]]+)\]:\s+([^\s]+)(?:\s+"[^"]*")?\s*$'

    reference_map: dict[str, str] = {}
    for match in re.finditer(reference_def_pattern, md_content, re.MULTILINE):
        reference_map[match.group(1).lower()] = match.group(2).strip()

    reference_positions: set[int] = set()
    for match in re.finditer(reference_usage_pattern, md_content):
        reference_positions.add(match.start())

    def replace_reference_image(match: re.Match[str]) -> str:
        nonlocal image_counter
        original_alt = match.group(1)
        ref_id = match.group(2)
        url = reference_map.get(ref_id.lower(), f"[unknown reference: {ref_id}]")
        new_alt = f"Image_{image_counter}"
        images.append({
            "original_text": match.group(0),
            "url": url,
            "alt_text": original_alt,
            "new_alt_text": new_alt,
            "image_type": "reference",
        })
        image_counter += 1
        return f"![{new_alt}][{ref_id}]"

    def replace_markdown_image(match: re.Match[str]) -> str:
        nonlocal image_counter
        if match.start() in reference_positions:
            return match.group(0)
        original_alt = match.group(1)
        url = match.group(2)
        new_alt = f"Image_{image_counter}"
        images.append({
            "original_text": match.group(0),
            "url": url,
            "alt_text": original_alt,
            "new_alt_text": new_alt,
            "image_type": "markdown",
        })
        image_counter += 1
        return f"![{new_alt}]({url})"

    def replace_html_image(match: re.Match[str]) -> str:
        nonlocal image_counter
        fragment = BeautifulSoup(match.group(0), "html.parser")
        tags = fragment.find_all(True)
        if len(tags) != 1 or tags[0].name != "img" or fragment.get_text():
            return match.group(0)
        img_tag = tags[0]
        src = img_tag.get("src", "")
        original_alt = img_tag.get("alt", "")
        original_text = str(img_tag)
        new_alt = f"Image_{image_counter}"
        img_tag["alt"] = new_alt
        images.append({
            "original_text": original_text,
            "url": src,
            "alt_text": original_alt,
            "new_alt_text": new_alt,
            "image_type": "html",
        })
        image_counter += 1
        return str(img_tag)

    # Only the <img> tags are re-serialised: running the whole document through
    # an HTML parser escapes "&", "<" and ">" and closes anything that looks like
    # a tag, which corrupts code, autolinks and blockquotes.
    def process_html_images(content: str) -> str:
        return _HTML_IMG_TAG_RE.sub(replace_html_image, content)

    modified = re.sub(reference_usage_pattern, replace_reference_image, md_content)
    modified = re.sub(markdown_img_pattern, replace_markdown_image, modified)
    modified = process_html_images(modified)
    return modified, images
