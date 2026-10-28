"""Turning an SVG from a document into a PNG, without the app's parser stack.

Kept to the standard library, ``cairosvg`` and ``defusedxml`` (and the error
type) so the long-lived renderer process of ``svg_worker`` starts quickly and
holds little memory. ``ImageParser`` delegates to these functions.
"""
from __future__ import annotations

import base64
import contextlib
import re
from typing import TYPE_CHECKING

from app.exceptions.indexing_exceptions import DocumentProcessingError

if TYPE_CHECKING:
    from collections.abc import Callable

VIEWBOX_NUM_COMPONENTS = 4

_CSS_VAR_OPEN = re.compile(r'var\(\s*--[\w-]+\s*(,)?')
# An SVG's author is whoever wrote the document, and cairosvg has no time limit
# of its own. So it is rasterised in a worker process with a deadline and a
# memory cap (``svg_worker``); this budget is a cheap filter in front of it:
# the decoded size, the element count and depth, what references expand to, and
# the size of the PNG it will draw.
_MAX_SVG_BYTES = 5 * 1024 * 1024
_MAX_SVG_ELEMENTS = 50_000
_MAX_SVG_DEPTH = 100
_MAX_SVG_EXPANDED_ELEMENTS = 200_000
_MAX_SVG_REFERENCES = 20_000
_MAX_SVG_OUTPUT_PIXELS = 32_000_000


def _local_name(tag: object) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


_CSS_URL_REF = re.compile(r"url\(\s*['\"]?\s*#([^'\")\s]+)")


def _svg_references(node: object) -> list[str]:
    """Ids this element draws again: ``href``/``xlink:href`` and any ``url(#id)``."""
    refs: list[str] = []
    for name, value in node.attrib.items():  # type: ignore[attr-defined]
        if _local_name(name) == "href":
            target = value.strip()
            if target.startswith("#"):
                refs.append(target[1:].strip())
        elif "url(" in value:
            refs.extend(_CSS_URL_REF.findall(value))
    if _local_name(node.tag) == "style" and node.text:  # type: ignore[attr-defined]
        refs.extend(_CSS_URL_REF.findall(node.text))  # type: ignore[attr-defined]
    return refs


class _OverBudget(Exception):
    def __init__(self, elements: int) -> None:
        super().__init__(elements)
        self.elements = elements


def _budget_builder() -> object:
    from xml.etree.ElementTree import TreeBuilder

    class BudgetBuilder(TreeBuilder):
        """Stops the parse at the first element past the count or depth limit, so
        refusing a huge document costs what parsing up to the limit costs."""

        def __init__(self) -> None:
            super().__init__()
            self.count = 0
            self.depth = 0

        def start(self, tag: str, attrs: dict[str, str]) -> object:
            self.count += 1
            self.depth += 1
            if self.count > _MAX_SVG_ELEMENTS or self.depth > _MAX_SVG_DEPTH:
                raise _OverBudget(self.count)
            return super().start(tag, attrs)

        def end(self, tag: str) -> object:
            self.depth -= 1
            return super().end(tag)

    return BudgetBuilder()


def check_svg_budget(svg_str: str) -> None:
    """Refuse an SVG that is too big, or draws too much, to rasterise.

    A cheap pre-filter in front of the worker process that bounds time and
    memory: entities are refused (``defusedxml``), and every reference
    (``<use>``, ``url(#id)`` in fill, mask, marker, clip-path, filter...) is
    counted as drawing its target again. Anything that is not well-formed XML
    is refused, as cairosvg could not read it either.
    """
    from defusedxml import ElementTree as safe_et
    from defusedxml.common import DefusedXmlException

    if len(svg_str) > _MAX_SVG_BYTES:
        raise DocumentProcessingError("SVG is too large to rasterise", details={"bytes": len(svg_str)})
    try:
        parser = safe_et.XMLParser(target=_budget_builder())
        parser.feed(svg_str.encode("utf-8"))
        root = parser.close()
    except _OverBudget as e:
        raise DocumentProcessingError("SVG is too complex to rasterise", details={"elements": e.elements}) from e
    except DefusedXmlException as e:
        raise DocumentProcessingError("SVG uses entities or DTDs", details={"error": str(e)}) from e
    except Exception as e:
        raise DocumentProcessingError("SVG is not well-formed", details={"error": str(e)}) from e

    ids: dict[str, object] = {}
    count = 0
    references = 0
    stack = [(root, 1)]
    while stack:
        node, depth = stack.pop()
        count += 1
        if count > _MAX_SVG_ELEMENTS or depth > _MAX_SVG_DEPTH:
            raise DocumentProcessingError("SVG is too complex to rasterise", details={"elements": count})
        node_id = node.get("id")
        if node_id:
            ids.setdefault(node_id.strip(), node)
        references += len(_svg_references(node))
        if references > _MAX_SVG_REFERENCES:
            raise DocumentProcessingError("SVG has too many references", details={"references": references})
        stack.extend((child, depth + 1) for child in node)

    memo: dict[int, int] = {}

    def expanded(node: object, active: frozenset[int]) -> int:
        key = id(node)
        if key in memo:
            return memo[key]
        if key in active:
            raise DocumentProcessingError("SVG references itself")
        inner = active | {key}
        total = 1 + sum(expanded(c, inner) for c in node)  # type: ignore[attr-defined]
        for ref in _svg_references(node):
            target = ids.get(ref)
            if target is not None:
                total += expanded(target, inner)
        if total > _MAX_SVG_EXPANDED_ELEMENTS:
            raise DocumentProcessingError("SVG expands to too much to rasterise", details={"elements": total})
        memo[key] = total
        return total

    try:
        expanded(root, frozenset())
    except RecursionError as e:
        raise DocumentProcessingError("SVG is nested too deeply to rasterise") from e


def extract_svg_dimensions(svg_str: str) -> tuple[int | None, int | None]:
    """
    Extract width and height from SVG content.
    Tries to get dimensions from width/height attributes, then from viewBox if not available.

    Args:
        svg_str: SVG content as a string

    Returns:
        Tuple of (width, height) in pixels, or (None, None) if not found
    """
    try:
        # Find the <svg> tag (case-insensitive)
        svg_tag_pattern = r'<svg[^>]*?>'
        match = re.search(svg_tag_pattern, svg_str, re.IGNORECASE)

        if not match:
            return None, None

        svg_tag = match.group(0)

        # Try to extract width attribute
        width_match = re.search(r'width\s*=\s*["\']?([^"\'>\s]+)', svg_tag, re.IGNORECASE)
        height_match = re.search(r'height\s*=\s*["\']?([^"\'>\s]+)', svg_tag, re.IGNORECASE)

        width = None
        height = None

        if width_match:
            width_str = width_match.group(1).strip()
            # Remove units (px, pt, etc.) and convert to int
            width_value = re.sub(r'[^\d.]', '', width_str)
            if width_value:
                with contextlib.suppress(ValueError):
                    width = int(float(width_value))

        if height_match:
            height_str = height_match.group(1).strip()
            # Remove units (px, pt, etc.) and convert to int
            height_value = re.sub(r'[^\d.]', '', height_str)
            if height_value:
                with contextlib.suppress(ValueError):
                    height = int(float(height_value))

        # If both found, return them
        if width and height:
            return width, height

        # If not found, try viewBox (format: "x y width height")
        viewbox_match = re.search(r'viewBox\s*=\s*["\']?([^"\'>]+)', svg_tag, re.IGNORECASE)
        if viewbox_match:
            viewbox_str = viewbox_match.group(1).strip()
            # Extract numbers from viewBox
            viewbox_numbers = re.findall(r'[-+]?\d*\.?\d+', viewbox_str)
            if len(viewbox_numbers) >= VIEWBOX_NUM_COMPONENTS:
                try:
                    vb_width = int(float(viewbox_numbers[2]))
                    vb_height = int(float(viewbox_numbers[3]))
                    # Use viewBox dimensions if width/height not found
                    if not width:
                        width = vb_width
                    if not height:
                        height = vb_height
                except (ValueError, IndexError):
                    pass

        return width, height

    except Exception:
        return None, None


def resolve_css_var_fallbacks(svg_str: str) -> str:
    """
    Replace each CSS ``var(--name, fallback)`` with its fallback, as a browser
    does when the custom property is not defined.

    cairosvg has no custom-property support, and its color parser finds a hex
    color anywhere in the value but slices it from index 1, so
    ``var(--fill-0, #B4B4B4)`` fails with "invalid literal for int() with
    base 16: 'ar'". A ``var()`` without a fallback, or one whose parentheses
    never close, is left as is.

    One pass with an explicit stack: the SVG comes from a document, so deep
    ``var()`` nesting must not recurse or rescan.
    """
    if 'var(' not in svg_str:
        return svg_str
    out: list[str] = []
    # One frame per open var(): [has_fallback, open parens inside it,
    # input index of "var(", output length at "var("].
    frames: list[list] = []
    i = 0
    n = len(svg_str)
    while i < n:
        char = svg_str[i]
        match = _CSS_VAR_OPEN.match(svg_str, i) if char == 'v' else None
        if match:
            has_fallback = match.group(1) is not None
            frames.append([has_fallback, 0, i, len(out)])
            i = match.end()
            if has_fallback:
                while i < n and svg_str[i].isspace():
                    i += 1
            else:
                out.extend(match.group(0))
            continue
        if frames and char == '(':
            frames[-1][1] += 1
        elif frames and char == ')':
            if frames[-1][1]:
                frames[-1][1] -= 1
            else:
                has_fallback, _, _, out_start = frames.pop()
                if has_fallback:
                    while len(out) > out_start and out[-1].isspace():
                        out.pop()
                    i += 1
                    continue
        out.append(char)
        i += 1
    if frames:
        _, _, in_start, out_start = frames[0]
        return ''.join(out[:out_start]) + svg_str[in_start:]
    return ''.join(out)


def sanitize_svg_content(svg_str: str) -> bytes:
    """
    Sanitize SVG content to fix common XML parsing issues, including unbound namespace prefixes.
    This function ensures that common SVG namespace declarations are present in the root <svg> element,
    and replaces CSS var() values that cairosvg cannot read with their fallbacks.

    Args:
        svg_str: SVG content as a string

    Returns:
        Sanitized SVG content as bytes
    """
    try:
        svg_str = resolve_css_var_fallbacks(svg_str)

        # Find the <svg> tag (case-insensitive)
        svg_tag_pattern = r'(<svg[^>]*?)>'
        match = re.search(svg_tag_pattern, svg_str, re.IGNORECASE)

        if not match:
            # If no <svg> tag found, return as-is
            return svg_str.encode('utf-8')

        svg_tag = match.group(1)
        fixed_tag = svg_tag

        # Add xmlns if completely missing
        if 'xmlns=' not in fixed_tag.lower() and 'xmlns:' not in fixed_tag.lower():
            fixed_tag += ' xmlns="http://www.w3.org/2000/svg"'
        # Ensure default SVG namespace is present
        elif (
            'xmlns="' not in fixed_tag
            and 'xmlns=\'' not in fixed_tag
            # it may have xmlns:something but not xmlns itself
            and not re.search(r'xmlns\s*=\s*["\']', fixed_tag, re.IGNORECASE)
        ):
            fixed_tag += ' xmlns="http://www.w3.org/2000/svg"'

        # Add xlink namespace if xlink: is used anywhere but namespace not declared
        if re.search(r'\bxlink:', svg_str, re.IGNORECASE) and 'xmlns:xlink=' not in fixed_tag.lower():
            fixed_tag += ' xmlns:xlink="http://www.w3.org/1999/xlink"'

        # If we made changes, replace the original tag
        if fixed_tag != svg_tag:
            svg_str = svg_str[:match.start()] + fixed_tag + '>' + svg_str[match.end():]

    except Exception:
        # If sanitization fails, return original content
        # cairosvg might still be able to handle it or will give a clearer error
        pass

    return svg_str.encode('utf-8')


def convert(
    svg_base64: str,
    output_width: int | None = None,
    output_height: int | None = None,
    scale: float = 1.0,
    background_color: str | None = None,
    svg2png: Callable[..., bytes] | None = None,
) -> str:
    """
    Convert SVG base64 string to PNG base64 string.

    Args:
        svg_base64: Base64 encoded SVG string (with or without data URI prefix)
        output_width: Desired output width in pixels (optional)
        output_height: Desired output height in pixels (optional)
        scale: Scale factor for the output (default: 1.0)
        background_color: Background color for transparent areas (e.g., 'white', '#FFFFFF')

    Returns:
        Base64 encoded PNG string

    Raises:
        ValueError: If the input is not valid base64 or SVG
        Exception: If conversion fails
    """
    try:
        # Remove data URI prefix if present
        if svg_base64.startswith('data:image/svg+xml;base64,'):
            svg_base64 = svg_base64.replace('data:image/svg+xml;base64,', '')

        # Decode base64 to get SVG content
        svg_data = base64.b64decode(svg_base64)

        # Verify it's actually SVG content
        svg_str = svg_data.decode('utf-8')
        if not ('<svg' in svg_str.lower() or '<?xml' in svg_str.lower()):
            raise DocumentProcessingError(
                "Decoded content does not appear to be valid SVG",
                details={"content_preview": svg_str[:100]},
            )

        if len(svg_str) > _MAX_SVG_BYTES:
            raise DocumentProcessingError("SVG is too large to rasterise", details={"bytes": len(svg_str)})

        # Extract dimensions from SVG if not provided
        final_width = output_width
        final_height = output_height

        if not final_width or not final_height:
            svg_width, svg_height = extract_svg_dimensions(svg_str)

            # Use extracted dimensions if not provided
            if not final_width:
                final_width = svg_width
            if not final_height:
                final_height = svg_height

            # If still no dimensions found, use defaults
            # Default to 800x600 if no dimensions available
            if not final_width:
                final_width = 800
            if not final_height:
                final_height = 600

        if (final_width or 0) * (final_height or 0) * scale * scale > _MAX_SVG_OUTPUT_PIXELS:
            raise DocumentProcessingError(
                "SVG would be rasterised too large",
                details={"width": final_width, "height": final_height},
            )

        # Sanitize SVG content to fix unbound namespace prefixes and other XML issues
        sanitized_svg_data = sanitize_svg_content(svg_str)
        # The budget is applied to what cairosvg will read, the repaired text.
        check_svg_budget(sanitized_svg_data.decode("utf-8"))

        # Convert SVG to PNG using cairosvg
        # cairosvg requires explicit dimensions when SVG doesn't have them
        if svg2png is None:
            raise DocumentProcessingError(
                "SVG conversion dependency missing: cairosvg is not installed",
                details={"dependency": "cairosvg"},
            )
        png_data = svg2png(
            bytestring=sanitized_svg_data,
            output_width=final_width,
            output_height=final_height,
            scale=scale,
            background_color=background_color
        )

        # Encode PNG to base64
        return base64.b64encode(png_data).decode('utf-8')

    except base64.binascii.Error as e:
        raise DocumentProcessingError(
            f"Invalid base64 input: {e}",
            details={"error": str(e)},
        ) from e
    except UnicodeDecodeError as e:
        raise DocumentProcessingError(
            f"Cannot decode SVG content: {e}",
            details={"error": str(e)},
        ) from e
    except DocumentProcessingError:
        raise
    except Exception as e:
        raise DocumentProcessingError(
            f"SVG to PNG conversion failed: {e}",
            details={"error": str(e)},
        ) from e
