"""URL resolver registry for the ``lookup_record`` tool.

Turns an arbitrary web URL (pasted by a user or emitted by an LLM) into a
:class:`CanonicalRef` that graph providers can resolve via an *indexed*
lookup, instead of relying on exact ``webUrl`` string matching.

Canonical-ID extraction beats URL string matching: the Confluence URL
``.../pages/450625553/Agent+Loop+Implementation`` contains ``450625553``,
which *is* the record's ``externalRecordId`` — immune to slug changes when
the page gets renamed, and resolvable via the existing
``externalRecordId+connectorId`` composite index rather than a full scan.

Extractors are pure functions tried in order; the first match wins. When no
extractor matches (e.g. a Confluence short link ``/wiki/x/{short}``, or an
unrecognized host), callers fall back to :func:`normalize_weburl` +
``get_record_by_weburl``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse, urlunparse

# --------------------------------------------------------------------------
# CanonicalRef
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CanonicalRef:
    """A canonical identifier extracted from a URL, ready for an indexed lookup."""

    kind: str  # "issue_key" | "external_id" | "slack_ts"
    value: str
    connector_family: str | None = None  # hint for narrowing connector search, e.g. "JIRA"
    extra: dict[str, str] | None = None  # e.g. {"channel_id": "..."} for Slack


# --------------------------------------------------------------------------
# Per-connector extractors
# --------------------------------------------------------------------------

_JIRA_BROWSE_RE = re.compile(r"/browse/([A-Za-z][A-Za-z0-9]{1,9}-\d+)")
_CONFLUENCE_PAGE_RE = re.compile(r"/wiki/spaces/[^/]+/pages/(\d+)")
_CONFLUENCE_LEGACY_RE = re.compile(r"pageId=(\d+)")
_DRIVE_FILE_RE = re.compile(r"/(?:file|document|spreadsheets|presentation)/d/([a-zA-Z0-9_-]{10,})")
_DRIVE_OPEN_ID_RE = re.compile(r"(?:^|[?&])id=([a-zA-Z0-9_-]{10,})(?:&|$)")
_SLACK_ARCHIVE_RE = re.compile(r"/archives/([A-Za-z0-9._-]+)/p(\d{10,})")
_LINEAR_ISSUE_RE = re.compile(r"linear\.app/[^/]+/issue/([A-Za-z][A-Za-z0-9]*-\d+)")
_NOTION_HEX32_RE = re.compile(r"([0-9a-fA-F]{32})")
_ISSUE_KEY_STRICT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]{1,9}-\d+$")


def _resolve_jira(parsed) -> CanonicalRef | None:
    m = _JIRA_BROWSE_RE.search(parsed.path)
    if m:
        return CanonicalRef(kind="issue_key", value=m.group(1).upper(), connector_family="JIRA")
    # Board/backlog URLs carry the issue key in a query param instead of the path.
    qs = parse_qs(parsed.query)
    for key in ("selectedIssue", "selectedissue"):
        values = qs.get(key)
        if values and _ISSUE_KEY_STRICT_RE.match(values[0]):
            return CanonicalRef(kind="issue_key", value=values[0].upper(), connector_family="JIRA")
    return None


def _resolve_confluence(parsed) -> CanonicalRef | None:
    m = _CONFLUENCE_PAGE_RE.search(parsed.path)
    if m:
        return CanonicalRef(kind="external_id", value=m.group(1), connector_family="CONFLUENCE")
    m = _CONFLUENCE_LEGACY_RE.search(parsed.query)
    if m:
        return CanonicalRef(kind="external_id", value=m.group(1), connector_family="CONFLUENCE")
    # /wiki/x/{shortlink} short links aren't decodable offline — fall through to webUrl match.
    return None


def _resolve_drive(parsed) -> CanonicalRef | None:
    m = _DRIVE_FILE_RE.search(parsed.path)
    if m:
        return CanonicalRef(kind="external_id", value=m.group(1), connector_family="DRIVE")
    m = _DRIVE_OPEN_ID_RE.search(parsed.query)
    if m:
        return CanonicalRef(kind="external_id", value=m.group(1), connector_family="DRIVE")
    return None


def _resolve_slack(parsed) -> CanonicalRef | None:
    m = _SLACK_ARCHIVE_RE.search(parsed.path)
    if not m:
        return None
    channel_id, raw_ts = m.group(1), m.group(2)
    # Slack permalink timestamps drop the decimal point: p1720000000000100 -> 1720000000.000100
    ts = f"{raw_ts[:10]}.{raw_ts[10:]}" if len(raw_ts) > 10 else raw_ts
    qs = parse_qs(parsed.query)
    thread_ts = qs.get("thread_ts", [None])[0]
    return CanonicalRef(
        kind="slack_ts",
        value=thread_ts or ts,
        connector_family="SLACK",
        extra={"channel_id": channel_id, "ts": ts},
    )


def _resolve_linear(url: str) -> CanonicalRef | None:
    m = _LINEAR_ISSUE_RE.search(url)
    if m:
        return CanonicalRef(kind="issue_key", value=m.group(1).upper(), connector_family="LINEAR")
    return None


def _resolve_notion(parsed) -> CanonicalRef | None:
    # Trailing 32-hex page ID in the slug (Notion strips dashes in URLs).
    tail = parsed.path.rsplit("-", 1)[-1].rsplit("/", 1)[-1]
    m = _NOTION_HEX32_RE.fullmatch(tail)
    if not m:
        return None
    hexid = m.group(1)
    dashed = f"{hexid[0:8]}-{hexid[8:12]}-{hexid[12:16]}-{hexid[16:20]}-{hexid[20:]}"
    return CanonicalRef(kind="external_id", value=dashed, connector_family="NOTION")


def resolve_canonical_ref(url: str) -> CanonicalRef | None:
    """Try each per-connector extractor in order; return the first match, or ``None``."""
    try:
        parsed = urlparse(url)
    except Exception:
        return None

    for resolver in (_resolve_jira, _resolve_confluence, _resolve_drive, _resolve_slack, _resolve_notion):
        try:
            ref = resolver(parsed)
        except Exception:
            continue
        if ref:
            return ref

    try:
        ref = _resolve_linear(url)
    except Exception:
        ref = None
    return ref


# --------------------------------------------------------------------------
# URL normalization (fallback when no extractor matches)
# --------------------------------------------------------------------------

# Hosts whose query params carry the canonical ID and must NEVER be stripped.
SIGNIFICANT_QUERY_PARAM_HOSTS: dict[str, tuple[str, ...]] = {
    "service-now.com": ("sys_id",),
    "servicenow.com": ("sys_id",),
    "sharepoint.com": ("id", "sourcedoc"),
}

_TRACKING_PARAM_PREFIXES = ("utm_",)
_TRACKING_PARAMS_EXACT = {"atlOrigin", "usp", "gclid", "ref", "source", "fbclid", "igshid"}
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _is_tracking_param(name: str) -> bool:
    return name in _TRACKING_PARAMS_EXACT or any(name.startswith(p) for p in _TRACKING_PARAM_PREFIXES)


def _significant_params_for_host(host: str) -> tuple[str, ...]:
    for suffix, params in SIGNIFICANT_QUERY_PARAM_HOSTS.items():
        if host == suffix or host.endswith("." + suffix):
            return params
    return ()


def normalize_weburl(url: str) -> str:
    """Strip fragment/tracking params/trailing slash; always preserve significant params.

    Used only as the fallback when no canonical-ID extractor matches.
    Callers should try the RAW url against ``get_record_by_weburl`` first
    (stored ``webUrl`` values are not normalized) and only fall back to this
    normalized form on a miss — it catches copy-pasted variants carrying
    tracking params, a stray ``#fragment``, or a trailing slash.
    """
    if not url:
        return url
    try:
        parsed = urlparse(url.strip())
    except Exception:
        return url

    scheme = (parsed.scheme or "https").lower()
    host = (parsed.hostname or "").lower()
    port = parsed.port
    netloc = host if (not port or port == _DEFAULT_PORTS.get(scheme)) else f"{host}:{port}"

    significant = _significant_params_for_host(host)
    query = ""
    if parsed.query:
        kept = [
            (k, v[0])
            for k, v in parse_qs(parsed.query, keep_blank_values=True).items()
            if k in significant or not _is_tracking_param(k)
        ]
        query = "&".join(f"{k}={v}" for k, v in kept)

    path = parsed.path
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]

    return urlunparse((scheme, netloc, path, "", query, ""))
