"""Blocks write, execute and egress tools from acting on literals or hosts only
another participant supplied.

Registered on PRE_TOOL_USE only for collaborative chats.
"""

from __future__ import annotations

import ipaddress
import json
import re
import socket
import unicodedata
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel, Field

from app.modules.agents.collaboration.tool_effects import (
    EXECUTE_TAG,
    ToolEffect,
    classify_tool,
)
from app.modules.agents.collaboration.tool_effects import (
    WRITE_TYPE_TAGS as _WRITE_ONLY_TAGS,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.agent_loop_lib.hooks.middleware.context import ToolCallContext
    from app.agent_loop_lib.hooks.middleware.pipeline import Next
    from app.agent_loop_lib.tools.base import Tag
    from app.modules.agents.collaboration.models import CollaborationContext

# `execute`: code-execution tools (`run_code`) can send a participant's URL or address out.
WRITE_TYPE_TAGS: Final = _WRITE_ONLY_TAGS | {EXECUTE_TAG}
ASK_TOOL_NAME_FRAGMENT: Final = "ask_user_question"
ASK_TOOL_PATH: Final = "internaltools__ask_user_question"
_MAX_REPORTED: Final = 5
_MAX_LITERAL_CHARS: Final = 80
_MAX_WALK_DEPTH: Final = 12

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")
_URL_RE = re.compile(r"""\b(?:https?|ftp)://[^\s<>"'`)\]]+""", re.IGNORECASE)
_HANDLE_RE = re.compile(r"(?<![\w.@])@([A-Za-z0-9_][A-Za-z0-9_.\-]{1,})")
_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-]{6,}")
# Unicode labels too: `évil.com` reaches xn--vil-9la.com, so both sides compare in punycode.
_DOMAIN_RE = re.compile(
    r"(?<![\w.\-])(?:[^\W_](?:[\w\-]{0,61}[^\W_])?\.)+(?:[^\W\d_]{2,24}|xn--[a-z0-9\-]{1,59})(?![\w\-])",
    re.IGNORECASE,
)
_IPV4_RE = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?!\w|\.\d)")
_IPV6_RE = re.compile(r"(?<![\w:.])(?:[0-9a-f]{0,4}:){2,7}(?:[0-9a-f]{1,4}|(?:\d{1,3}\.){3}\d{1,3})?(?![\w:]|\.\d)", re.IGNORECASE)
# inet_aton spellings (`16909060`, `0x01020304`, `0177.1`) that resolvers map to an IPv4 address.
_NUMERIC_HOST_RE = re.compile(r"^(?:0x[0-9a-f]+|\d+)(?:\.(?:0x[0-9a-f]+|\d+)){0,3}$", re.IGNORECASE)
_DEFANG_RE = re.compile(
    r"\s*(?:[\[({]\s*\.\s*[\])}]|[\[({]\s*dot\s*[\])}]|\s\bdot\b\s)\s*|(?<=\w) +\. +(?=\w)", re.IGNORECASE,
)
# IDNA label separators that NFKC leaves alone but resolvers treat as `.`.
_IDNA_DOTS: Final = str.maketrans({"\u3002": ".", "\uff61": ".", "\uff0e": "."})
# Common file extensions that are not delegated TLDs, so `report.pdf` is not a host.
# Extensions that are real TLDs (`.md`, `.py`, `.sh`, `.zip`, `.mov`, `.app`) stay hosts.
_FILE_EXTENSIONS: Final = frozenset({
    "pdf", "csv", "tsv", "json", "txt", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "png", "jpg", "jpeg",
    "gif", "svg", "webp", "html", "htm", "xml", "yaml", "yml", "toml", "ini", "cfg", "log", "js", "jsx",
    "tsx", "ipynb", "sql", "tar", "tgz", "gz", "exe", "dll", "jar", "bak",
})
_PERCENT_DECODE_PASSES: Final = 2


def is_write_tool(tool_path: str, tags: tuple[Tag, ...]) -> bool:
    return classify_tool(tool_path, tags) in (ToolEffect.WRITE, ToolEffect.EXECUTE)


def _normalize_url(url: str) -> str:
    url = url.rstrip(".,;:!?").split("#", 1)[0].lower()
    return url.rstrip("/")


def _looks_like_id(token: str) -> bool:
    # Plain words must not count: require a digit, or long mixed-case.
    if any(ch.isdigit() for ch in token):
        return True
    return len(token) >= 10 and token != token.lower() and token != token.upper() and token[1:] != token[1:].lower()


def _fold(text: str) -> str:
    # Fold fullwidth forms and drop zero-width/format characters, which the model
    # would normalise away when it writes the argument (`ｘ＠evil．com`, `x\u200b@evil.com`).
    return "".join(ch for ch in unicodedata.normalize("NFKC", text) if unicodedata.category(ch) != "Cf")


def extract_literals(value: object) -> set[str]:
    """Emails, URLs, @handles and id-like tokens found in any string inside
    `value`, lowercased. URLs ignore fragment and trailing slash."""
    out: set[str] = set()
    _walk(value, out, 0)
    return out


def extract_hosts(value: object, *, defang: bool = False) -> set[str]:
    """Hosts of URLs and bare domains (also the domain part of emails) in any
    string inside `value`. Percent-encoding is decoded twice so a URL nested in
    a query parameter is seen. `defang` also un-obfuscates `[.]`, `(.)` and ` dot `."""
    out: set[str] = set()
    _walk(value, out, 0, lambda text, acc: _scan_hosts(text, acc, defang=defang))
    return out


def canonical_host(host: str) -> str:
    """One spelling per destination: IP literals in standard form (IPv4-mapped IPv6 and
    inet_aton numerals as dotted IPv4), names lowercased and IDNA-encoded."""
    host = _fold(host).translate(_IDNA_DOTS).strip("[]").rstrip(".").lower()
    if _NUMERIC_HOST_RE.match(host):
        try:
            return socket.inet_ntoa(socket.inet_aton(host))
        except OSError:
            return host
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        mapped = getattr(ip, "ipv4_mapped", None)
        return str(mapped or ip)
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError:
        return host


def _bare_domain(host: str) -> str | None:
    head, _, tld = host.rpartition(".")
    if tld not in _FILE_EXTENSIONS:
        return host
    return head if "." in head else None


def _scan_hosts(text: str, out: set[str], *, defang: bool) -> None:
    text = _fold(text)
    for _ in range(_PERCENT_DECODE_PASSES):
        text = unquote(text)
    text = _fold(text).translate(_IDNA_DOTS)
    if defang:
        text = _DEFANG_RE.sub(".", text)
    for m in _URL_RE.finditer(text):
        try:
            host = urlsplit(m.group(0)).hostname
        except ValueError:
            continue
        if host:
            out.add(canonical_host(host))
    for m in _DOMAIN_RE.finditer(text):
        if domain := _bare_domain(canonical_host(m.group(0))):
            out.add(domain)
    for m in _IPV4_RE.finditer(text):
        out.add(canonical_host(m.group(0)))
    for m in _IPV6_RE.finditer(text):
        candidate = m.group(0)
        try:
            ipaddress.IPv6Address(candidate)
        except ValueError:
            continue
        out.add(canonical_host(candidate))


# Shared mailbox providers are never "the org's own domain": exempting them would let anyone
# exfiltrate to a gmail.com address. Deliberately small; unlisted webmail just costs a card.
_PUBLIC_MAIL_DOMAINS: Final = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com",
    "yahoo.com", "icloud.com", "me.com", "aol.com", "proton.me", "protonmail.com", "gmx.com", "mail.com",
    "pm.me", "zoho.com", "yandex.com", "yandex.ru", "mail.ru", "fastmail.com", "tutanota.com", "hey.com",
    "qq.com", "163.com", "126.com", "naver.com", "gmx.de", "web.de", "rediffmail.com",
})


def internal_domain_of(email: str | None) -> str | None:
    """Domain of the sender's own address, unless it is a public mail provider. Every
    participant belongs to the sender's org, so mail domains of colleagues are internal."""
    domain = _fold(email or "").rpartition("@")[2].strip()
    domain = canonical_host(domain) if domain else ""
    if not domain or "." not in domain or any(_is_subhost(domain, d) for d in _PUBLIC_MAIL_DOMAINS):
        return None
    return domain


def _is_subhost(host: str, parent: str) -> bool:
    return host == parent or host.endswith("." + parent)


def foreign_hosts(tool_input: object, index: ProvenanceIndex) -> list[str]:
    """Hosts in the call that equal, or sit under, a host only another participant named."""
    return sorted(
        h
        for h in extract_hosts(tool_input)
        if h not in index.sender_hosts
        and not any(_is_subhost(h, i) for i in index.internal_hosts)
        and any(_is_subhost(h, o) for o in index.other_hosts)
    )


def _walk(value: object, out: set[str], depth: int, scan: Callable[[str, set[str]], None] | None = None) -> None:
    if depth > _MAX_WALK_DEPTH:
        return
    if isinstance(value, str):
        (scan or _scan)(value, out)
    elif isinstance(value, bool) or value is None:
        return
    elif isinstance(value, int):
        (scan or _scan)(str(value), out)
    elif isinstance(value, dict):
        for v in value.values():
            _walk(v, out, depth + 1, scan)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for v in value:
            _walk(v, out, depth + 1, scan)


def _scan(text: str, out: set[str]) -> None:
    text = _fold(text)
    for m in _URL_RE.finditer(text):
        out.add(_normalize_url(m.group(0)))
    for m in _EMAIL_RE.finditer(text):
        out.add(m.group(0).rstrip(".").lower())
    for m in _HANDLE_RE.finditer(text):
        out.add("@" + m.group(1).rstrip(".-").lower())
    for m in _TOKEN_RE.finditer(text):
        token = m.group(0).strip("-_")
        if len(token) >= 6 and _looks_like_id(token):
            out.add(token.lower())


class ProvenanceIndex(BaseModel):
    sender: set[str] = Field(default_factory=set)
    others: set[str] = Field(default_factory=set)
    sender_hosts: set[str] = Field(default_factory=set)
    other_hosts: set[str] = Field(default_factory=set)
    internal_hosts: set[str] = Field(default_factory=set)


def _ask_args_from_last_turn(prev: list[dict[str, Any]]) -> list[Any]:
    if not prev:
        return []
    results = prev[-1].get("tool_results")
    if not isinstance(results, list):
        return []
    return [
        r.get("args")
        for r in results
        if isinstance(r, dict) and ASK_TOOL_NAME_FRAGMENT in str(r.get("tool_name") or "")
    ]


def build_provenance(
    prev: list[dict[str, Any]],
    query: str,
    c: CollaborationContext,
    resume_answers: str | None,
    sender_email: str | None = None,
) -> ProvenanceIndex:
    """`sender`: current query, resume answers, the sender's own earlier turns
    and, on a resume, the args of the card being answered. `others`: other
    participants' user_query and note text only (assistant text is never counted).
    A user_query without `authorRef` is not attributable to the sender, so it
    counts as `others` (fail closed)."""
    index = ProvenanceIndex()
    if internal := internal_domain_of(sender_email):
        index.internal_hosts.add(internal)
        index.sender_hosts.add(internal)

    def _add_sender(value: object) -> None:
        _walk(value, index.sender, 0)
        index.sender_hosts |= extract_hosts(value)

    _add_sender(query)
    if resume_answers:
        _add_sender(resume_answers)
        _add_sender(_ask_args_from_last_turn(prev))
    for turn in prev:
        if turn.get("role") not in ("user_query", "note"):
            continue
        if turn.get("authorRef") == c.currentSenderRef:
            _add_sender(turn.get("content"))
        else:
            _walk(turn.get("content"), index.others, 0)
            index.other_hosts |= extract_hosts(turn.get("content"), defang=True)
    return index


def _refusal(literals: list[str]) -> str:
    shown = [lit[:_MAX_LITERAL_CHARS] for lit in literals[:_MAX_REPORTED]]
    return json.dumps(
        {
            "status": "blocked",
            "code": "collaboration_write_guard",
            "literals": shown,
            "message": (
                "This action uses values that only another participant of this shared chat "
                "provided, not the person who sent the current message. Do not retry with the "
                f"same values. Call {ASK_TOOL_PATH} to ask the current sender to confirm them "
                "first."
            ),
        },
        ensure_ascii=False,
    )


def collaboration_write_guard(index: ProvenanceIndex) -> Callable[[ToolCallContext, Next], Awaitable[None]]:
    async def _middleware(ctx: ToolCallContext, next_fn: Next) -> None:
        if classify_tool(ctx.tool_path, ctx.tags) is not ToolEffect.READ:
            foreign = sorted((extract_literals(ctx.tool_input) & index.others) - index.sender)
            foreign = foreign or foreign_hosts(ctx.tool_input, index)
            if foreign:
                ctx.deny(_refusal(foreign))
                return
        await next_fn()

    return _middleware
