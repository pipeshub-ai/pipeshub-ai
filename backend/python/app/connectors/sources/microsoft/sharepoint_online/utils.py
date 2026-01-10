"""
SharePoint HTML Cleaning Utilities

This module provides functions to clean SharePoint page HTML by removing
unnecessary attributes, empty elements, and vendor-specific styles.
"""

import re

from bs4 import BeautifulSoup

# =====================================================
# ATTRIBUTES TO STRIP (SharePoint bloat)
# =====================================================
ATTRS_TO_REMOVE = {
    # SharePoint Canvas/WebPart Data
    'data-sp-canvasdataversion',
    'data-sp-canvascontrol',
    'data-sp-controldata',
    'data-sp-webpartdata',
    'data-sp-webpart',
    'data-sp-webpartid',
    'data-sp-prop-name',
    'data-sp-original-src',
    'data-sp-a11y-id',
    'data-sp-feature-tag',
    'data-sp-feature-instance',

    # SharePoint RTE (Rich Text Editor)
    'data-sp-rte',
    'data-cke-saved-href',
    'data-cke-widget-data',
    'data-cke-widget-id',
    'data-cke-widget-upcasted',
    'data-cke-widget-wrapper',

    # SharePoint Image Attributes
    'data-sp-originalwidth',
    'data-sp-originalheight',
    'data-sp-containsembeddedlink',

    # SharePoint Layout/Section Attributes
    'data-automation-id',
    'data-viewport-id',
    'data-instanceid',
    'data-position',

    # Accessibility duplicates (optional)
    'aria-label',
    'aria-hidden',

    # Microsoft Specific
    'data-ms-clickableimage',
    'data-version',
    'data-onerror',
}

# Attributes that START WITH these prefixes (catch-all)
ATTR_PREFIXES_TO_REMOVE = (
    'data-sp-',
    'data-cke-',
    'data-pnp-',
    'data-ms-',
)


def strip_sharepoint_attributes(soup: BeautifulSoup) -> int:
    """
    Remove all SharePoint-specific attributes from all tags.

    Args:
        soup: BeautifulSoup object to clean

    Returns:
        Count of removed attributes
    """
    count = 0
    for tag in soup.find_all(True):
        attrs_to_delete = []

        for attr in list(tag.attrs.keys()):
            # Check exact match
            if attr in ATTRS_TO_REMOVE:
                attrs_to_delete.append(attr)
                continue

            # Check prefix match
            if attr.startswith(ATTR_PREFIXES_TO_REMOVE):
                attrs_to_delete.append(attr)

        for attr in attrs_to_delete:
            del tag[attr]
            count += 1

    return count


def remove_empty_divs(soup: BeautifulSoup) -> int:
    """
    Remove empty divs that SharePoint leaves behind.
    Run multiple passes since removing one can make its parent empty.

    Args:
        soup: BeautifulSoup object to clean

    Returns:
        Count of removed divs
    """
    removed = 0
    for _ in range(5):  # Max 5 passes
        empty_divs = soup.find_all(
            lambda tag: tag.name == 'div' and
            not tag.get_text(strip=True) and
            not tag.find(['img', 'video', 'iframe', 'table', 'input', 'button', 'svg'])
        )
        if not empty_divs:
            break
        for div in empty_divs:
            div.decompose()
            removed += 1
    return removed


def clean_inline_styles(soup: BeautifulSoup) -> None:
    """
    Simplify inline styles by removing vendor prefixes and MS-specific styles.

    Args:
        soup: BeautifulSoup object to clean
    """
    for tag in soup.find_all(style=True):
        style = tag['style']
        # Remove MS-specific styles
        style = re.sub(r'-ms-[^;]+;?', '', style)
        style = re.sub(r'-webkit-[^;]+;?', '', style)
        style = re.sub(r'mso-[^;]+;?', '', style)
        # Clean up extra whitespace/semicolons
        style = re.sub(r';\s*;', ';', style)
        style = style.strip().strip(';')

        if style:
            tag['style'] = style
        else:
            del tag['style']


def clean_html_output(soup: BeautifulSoup, logger=None) -> str:
    """
    Final cleanup pass - strips attributes, removes empty divs, cleans styles.

    Args:
        soup: BeautifulSoup object to clean
        logger: Optional logger instance for debug output

    Returns:
        Cleaned HTML string
    """
    # 1. Strip SharePoint attributes
    attrs_removed = strip_sharepoint_attributes(soup)
    if logger:
        logger.debug(f"Removed {attrs_removed} SharePoint attributes")

    # 2. Clean inline styles
    clean_inline_styles(soup)

    # 3. Remove empty divs
    divs_removed = remove_empty_divs(soup)
    if logger:
        logger.debug(f"Removed {divs_removed} empty divs")

    # 4. Convert to string and clean up whitespace
    output_html = str(soup)
    output_html = re.sub(r'\n\s*\n', '\n', output_html)

    return output_html
