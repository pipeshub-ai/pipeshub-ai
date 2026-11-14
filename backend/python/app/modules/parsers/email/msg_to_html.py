"""An Outlook .msg file as one HTML page: its headers, its body and a list of its attachments.

Runs in a parse worker (``parse_pool.run_blocking``): the file is an OLE compound document from
anywhere, and a damaged one must cost a worker, not the service. Every content problem is raised
as ``UnreadableFileError``, which pickles whole back to the parent.

The body is the first of: the HTML body; the HTML encapsulated in the compressed RTF body; the
plain-text body; the text of the RTF body. Remote images are removed from it, and the caller
parses the page with remote fetching off as well, so opening a mail never contacts its sender.
Attachments are listed by name, type and size; their contents are not read.
"""
from __future__ import annotations

import datetime as dt
import html
from typing import TYPE_CHECKING

from app.services.parsing.interface import UnreadableFileError, damaged_file_reason
from app.utils.env_config import env_int

if TYPE_CHECKING:
    from bs4 import BeautifulSoup
    from oxmsg import Message

MSG_MAX_BYTES = env_int("MSG_MAX_BYTES", 64 * 1024 * 1024)

_PID_RTF_COMPRESSED = 0x1009
_PID_RECIPIENT_TYPE = 0x0C15
_PID_DISPLAY_TO = 0x0E04
_PID_DISPLAY_CC = 0x0E03
_PID_ATTACH_FILENAME = 0x3704
_PID_ATTACH_SIZE = 0x0E20
_TO, _CC = 1, 2
# A FILETIME of 0 (an unsent draft) reads as 1601-01-01.
_NO_DATE_YEAR = 1601
_REMOTE_ATTRIBUTES = ("src", "srcset", "href", "xlink:href", "poster", "background")
# Tags that load or embed something from elsewhere, or style that can (url(...)).
_DROPPED_TAGS = (
    "script", "style", "head", "title", "meta", "link", "base", "iframe", "frame", "frameset",
    "object", "embed", "applet",
)
_IMAGE_TAGS = ("img", "picture", "source", "image", "input", "video", "audio", "body", "table", "td")


def _too_large_reason() -> str:
    megabytes = MSG_MAX_BYTES // (1024 * 1024)
    return f"This .msg file is larger than {megabytes} MB, so PipesHub doesn't read it."


def _remote(value: str) -> bool:
    return not value.strip().lower().startswith("data:")


def _strip_remote(soup: BeautifulSoup) -> None:
    for tag in soup.find_all(_IMAGE_TAGS):
        for attribute in _REMOTE_ATTRIBUTES:
            value = tag.get(attribute)
            if isinstance(value, str) and (tag.name != "body" or attribute == "background") and _remote(value):
                del tag[attribute]


def _body_html(fragment: str) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(fragment, "html.parser")
    _strip_remote(soup)
    for tag in soup.find_all(_DROPPED_TAGS):
        tag.decompose()
    for tag in soup.find_all(style=True):
        if "url(" in str(tag["style"]).lower().replace(" ", ""):
            del tag["style"]
    body = soup.find("body")
    return body.decode_contents() if body is not None else str(soup)


def _text_html(text: str) -> str:
    paragraphs = [p for p in text.replace("\r\n", "\n").split("\n\n") if p.strip()]
    return "".join(f"<p>{html.escape(p).replace(chr(10), '<br/>')}</p>" for p in paragraphs)


def _rtf(message: Message) -> bytes | None:
    compressed = message.properties.binary_prop_value(_PID_RTF_COMPRESSED)
    if not compressed:
        return None
    import compressed_rtf

    return compressed_rtf.decompress(compressed)


def _body(message: Message) -> str:
    from app.modules.parsers.email.rtf_deencapsulate import (
        deencapsulate_html,
        is_encapsulated_html,
    )

    if html_body := message.html_body:
        return _body_html(html_body)
    rtf = _rtf(message)
    if rtf and is_encapsulated_html(rtf):
        return _body_html(deencapsulate_html(rtf))
    if plain := message.body:
        return _text_html(plain)
    if rtf:
        from striprtf.striprtf import rtf_to_text

        return _text_html(rtf_to_text(rtf.decode("latin-1"), errors="ignore"))
    return ""


def _addresses(message: Message) -> tuple[list[str], list[str]]:
    to: list[str] = []
    cc: list[str] = []
    for recipient in message.recipients:
        kind = recipient.properties.int_prop_value(_PID_RECIPIENT_TYPE)
        name, address = recipient.name, recipient.email_address
        shown = f"{name} <{address}>" if name and address and name != address else (address or name)
        if not shown:
            continue
        if kind == _TO:
            to.append(shown)
        elif kind == _CC:
            cc.append(shown)
    if not to and not cc:
        props = message.properties
        to = [props.str_prop_value(_PID_DISPLAY_TO) or ""]
        cc = [props.str_prop_value(_PID_DISPLAY_CC) or ""]
    return [a for a in to if a], [a for a in cc if a]


def _attachments(message: Message) -> list[str]:
    listed = []
    for attachment in message.attachments:
        props = attachment.properties
        name = attachment.file_name or props.str_prop_value(_PID_ATTACH_FILENAME) or "attachment"
        size = props.int_prop_value(_PID_ATTACH_SIZE)
        detail = attachment.mime_type or "application/octet-stream"
        if size:
            detail = f"{detail}, {size:,} bytes"
        listed.append(f"<li>{html.escape(name)} ({html.escape(detail)})</li>")
    return listed


def _header(label: str, value: str | None) -> str:
    return f"<p><strong>{label}:</strong> {html.escape(value)}</p>" if value else ""


def _page(message: Message) -> str:
    subject = message.subject or ""
    sent: dt.datetime | None = message.sent_date
    to, cc = _addresses(message)
    attachments = _attachments(message)
    parts = [
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\">",
        f"<title>{html.escape(subject)}</title></head><body>",
        f"<h1>{html.escape(subject)}</h1>" if subject else "",
        _header("From", message.sender),
        _header("To", "; ".join(to)),
        _header("Cc", "; ".join(cc)),
        _header("Date", sent.astimezone(dt.UTC).isoformat() if sent and sent.year > _NO_DATE_YEAR else None),
        "<hr/>",
        _body(message),
    ]
    if attachments:
        parts += ["<h2>Attachments</h2><ul>", *attachments, "</ul>"]
    parts.append("</body></html>")
    return "".join(parts)


def msg_to_html(content: bytes) -> str:
    """*content*, an Outlook .msg file, as an HTML page. Raises only ``UnreadableFileError``
    for a problem with the file."""
    if len(content) > MSG_MAX_BYTES:
        raise UnreadableFileError("msg", _too_large_reason())
    damaged = UnreadableFileError("msg", damaged_file_reason("msg"))
    from oxmsg import Message

    try:
        message = Message.load(bytes(content))
        return _page(message)
    except MemoryError:
        raise
    except Exception as exc:
        # olefile and python-oxmsg raise whatever the structure trips (struct, Index, Key,
        # Value, Assertion, OSError, decode errors): any of them means this file cannot be read.
        raise damaged from exc
