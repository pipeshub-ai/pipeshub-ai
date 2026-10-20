"""Convert Atlassian Document Format (ADF) to plain markdown text.

Shared by the Confluence Cloud connector (inline comment text) and the Jira Cloud
connector (fallback when Jira's rendered HTML lost part of a description or comment).

The conversion is deliberately simple: it keeps every piece of text in the document,
including code blocks, and applies basic inline marks. It does not try to reproduce the
layout exactly (nested lists are flattened, media nodes are skipped).
"""
from datetime import datetime, timezone
from typing import Any


def apply_adf_marks(text: str, marks: list[dict[str, Any]]) -> str:
    """Apply markdown formatting for ADF text marks (strong, em, code, link, ...)."""
    if not marks:
        return text

    for mark in reversed(marks):
        mark_type = mark.get("type", "")
        attrs = mark.get("attrs") or {}

        if mark_type == "strong":
            text = f"**{text}**"
        elif mark_type == "em":
            text = f"*{text}*"
        elif mark_type == "code":
            text = f"`{text}`"
        elif mark_type == "strike":
            text = f"~~{text}~~"
        elif mark_type == "link":
            href = attrs.get("href", "")
            if href:
                text = f"[{text}]({href})"
        elif mark_type == "underline":
            text = f"<u>{text}</u>"
        elif mark_type == "textColor":
            color = attrs.get("color", "")
            if color:
                text = f'<span style="color: {color}">{text}</span>'

    return text


def _inline_node_text(node_type: str, attrs: dict[str, Any]) -> str:
    """Text for ADF inline nodes that carry their text in ``attrs`` instead of ``content``.

    Mentions use only the display text (never the account id), so no identifier leaks
    into indexed content.
    """
    if node_type == "mention":
        text = str(attrs.get("text") or "").strip()
        if not text:
            return ""
        return text if text.startswith("@") else f"@{text}"
    if node_type == "emoji":
        return str(attrs.get("text") or attrs.get("shortName") or "")
    if node_type == "status":
        return str(attrs.get("text") or "")
    if node_type in ("inlineCard", "blockCard", "embedCard"):
        return str(attrs.get("url") or "")
    if node_type == "date":
        timestamp = attrs.get("timestamp")
        try:
            return datetime.fromtimestamp(int(timestamp) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        except (TypeError, ValueError, OverflowError, OSError):
            return ""
    return ""


def adf_to_markdown(adf_content: object) -> str:
    """Convert an ADF document to simple markdown text.

    Returns an empty string when ``adf_content`` is not an ADF document (a dict).
    """
    if not adf_content or not isinstance(adf_content, dict):
        return ""

    def extract_text_recursive(node: dict[str, Any]) -> str:
        """Recursively extract text from ADF nodes."""
        if not isinstance(node, dict):
            return ""

        node_type = node.get("type", "")

        if node_type == "text":
            text = node.get("text", "")
            marks = node.get("marks", [])
            return apply_adf_marks(text, marks)

        elif node_type == "paragraph":
            content = node.get("content", [])
            para_text = "".join(extract_text_recursive(child) for child in content).strip()
            return f"{para_text}\n\n" if para_text else ""

        elif node_type == "heading":
            level = (node.get("attrs") or {}).get("level", 1)
            content = node.get("content", [])
            heading_text = "".join(extract_text_recursive(child) for child in content).strip()
            return f"{'#' * level} {heading_text}\n\n" if heading_text else ""

        elif node_type in ["bulletList", "orderedList"]:
            content = node.get("content", [])
            is_numbered = node_type == "orderedList"
            items = []
            for i, child in enumerate(content, 1):
                item_text = extract_text_recursive(child).strip()
                if item_text:
                    prefix = f"{i}. " if is_numbered else "- "
                    items.append(f"{prefix}{item_text}")
            return "\n".join(items) + "\n\n" if items else ""

        elif node_type == "listItem":
            content = node.get("content", [])
            return "".join(extract_text_recursive(child) for child in content).strip()

        elif node_type == "codeBlock":
            content = node.get("content", [])
            code_text = "".join(child.get("text", "") for child in content if child.get("type") == "text")
            language = (node.get("attrs") or {}).get("language") or ""
            return f"```{language}\n{code_text}\n```\n\n" if code_text else ""

        elif node_type == "hardBreak":
            return "\n"

        elif node_type == "rule":
            return "---\n\n"

        elif "content" in node:
            content = node.get("content", [])
            return "".join(extract_text_recursive(child) for child in content)

        return _inline_node_text(node_type, node.get("attrs") or {})

    text_parts: list[str] = []
    if "content" in adf_content:
        text_parts.extend(
            extract_text_recursive(node) for node in adf_content["content"]
        )

    return "".join(text_parts).strip()
