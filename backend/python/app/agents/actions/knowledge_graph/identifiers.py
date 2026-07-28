"""Pure identifier parsing — no I/O, no side effects.

Converts raw user input (URLs, issue keys, external IDs) into structured
``CanonicalRef`` objects that downstream resolver code can act on.

Ported from the old ``url_resolver.py`` on branch ``tool-external-id``
and cleaned up for the agent-loop architecture.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from urllib.parse import parse_qs, unquote, urlparse


class RefKind(str, Enum):
    """What kind of identifier was extracted."""
    ISSUE_KEY = "issue_key"          # Jira-style KEY-123
    EXTERNAL_ID = "external_id"      # bare external/numeric id
    SLACK_TS = "slack_ts"            # Slack channel + timestamp
    WEB_URL = "web_url"              # raw URL that maps to a webUrl index lookup
    DRIVE_FILE = "drive_file"        # Google Drive file id (resolved via externalRecordId)


@dataclass
class CanonicalRef:
    """Structured result of parsing one identifier string."""
    kind: RefKind
    value: str              # the extracted key / id / ts / url
    connector_hint: str | None = None   # e.g. "JIRA", "CONFLUENCE", "SLACK"
    channel_id: str | None = None       # Slack only


# ---------------------------------------------------------------------------
# URL normalisation helpers
# ---------------------------------------------------------------------------

# Tracking / session parameters that add noise but do not change identity.
_TRACKING_PARAMS = frozenset([
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "atlOrigin", "atl_token", "source", "ref", "referrer", "cid",
    "fbclid", "gclid", "msclkid", "_hsenc", "_hsmi", "hsCtaTracking",
])


def normalize_weburl(raw: str) -> str:
    """Strip tracking params, fragment, and trailing slash for DB comparison.

    Does NOT fetch the URL — purely string manipulation.
    Applied before webUrl DB lookups so a URL with tracking params or a
    trailing slash still matches a clean stored value (and vice versa).
    """
    try:
        from urllib.parse import urlencode
        p = urlparse(raw)
        qs = parse_qs(p.query, keep_blank_values=True)
        cleaned = {k: v for k, v in qs.items() if k not in _TRACKING_PARAMS}
        clean_qs = urlencode(cleaned, doseq=True)
        path = p.path.rstrip("/") if p.path != "/" else p.path
        return p._replace(path=path, query=clean_qs, fragment="").geturl()
    except Exception:
        return raw


# ---------------------------------------------------------------------------
# Connector-specific URL patterns
# ---------------------------------------------------------------------------

# Jira: /browse/KEY-123  or  ?selectedIssue=KEY-123  or  /issues/KEY-123
_JIRA_BROWSE = re.compile(r"/browse/([A-Z][A-Z0-9_]+-\d+)", re.IGNORECASE)
_JIRA_SELECTED = re.compile(r"(?:^|[?&])selectedIssue=([A-Z][A-Z0-9_]+-\d+)", re.IGNORECASE)
_JIRA_ISSUE_PATH = re.compile(r"/issues?/([A-Z][A-Z0-9_]+-\d+)", re.IGNORECASE)

# Confluence: /pages/<numeric-id>/ or ?pageId=<numeric-id> or /wiki/x/<base62>
_CONFLUENCE_PAGE_PATH = re.compile(r"/pages/(\d+)", re.IGNORECASE)
_CONFLUENCE_PAGE_QS = re.compile(r"(?:^|[?&])pageId=(\d+)", re.IGNORECASE)
_CONFLUENCE_WIKI_SHORT = re.compile(r"/wiki/x/([A-Za-z0-9_-]+)", re.IGNORECASE)

# Google Drive: /d/<id>  or  open?id=<id>
_DRIVE_FILE_PATH = re.compile(r"/(?:document|spreadsheets|presentation|file)(?:s)?/d/([A-Za-z0-9_-]{25,})")
_DRIVE_OPEN_QS = re.compile(r"(?:^|[?&])id=([A-Za-z0-9_-]{25,})")

# Slack: /archives/<channel>/p<ts_no_dot>  or  /messages/<channel>/p<ts>
_SLACK_PERMALINK = re.compile(
    r"/(?:archives|messages)/([A-Z0-9]+)/p(\d{10})(\d{6})?", re.IGNORECASE
)

# Linear: /issue/TEAM-123 or /TEAM/TEAM-123
_LINEAR_ISSUE = re.compile(r"/(?:issue|[A-Z]{2,10})/([A-Z]{2,10}-\d+)", re.IGNORECASE)

# Notion: 32-char hex (no dashes) or UUID with dashes
_NOTION_UUID = re.compile(r"/([0-9a-f]{32})$|/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$", re.IGNORECASE)

# ServiceNow: ?sys_id=<32 hex>
_SERVICENOW_SYS_ID = re.compile(r"(?:^|[?&])sys_id=([0-9a-f]{32})", re.IGNORECASE)

# SharePoint: ?id=<path>  (preserve as external_id)
_SHAREPOINT_ITEM_QS = re.compile(r"(?:^|[?&])id=([^&]+)")

# Bare issue key (no URL context)
_BARE_ISSUE_KEY = re.compile(r"^([A-Z][A-Z0-9]{1,9}-\d+)$")

# Hosts that index by URL (no canonical id extractable from path)
_WEBURL_ONLY_HOSTS = frozenset([
    "mail.google.com",
    "outlook.live.com",
    "outlook.office.com",
    "outlook.office365.com",
])


def _is_url(text: str) -> bool:
    return text.startswith(("http://", "https://"))


def _domain_matches(host: str, domain: str) -> bool:
    """True if ``host`` is exactly ``domain`` or a subdomain of it.

    Anchors on the label boundary so ``evil-atlassian.net.attacker.com``
    does not match ``atlassian.net`` (a plain substring check would).
    """
    return host == domain or host.endswith("." + domain)


def _has_label(host: str, label: str) -> bool:
    """True if one of ``host``'s dot-separated labels equals ``label``.

    Heuristic match for self-hosted instances (e.g. ``jira.company.com``),
    anchored to a full label so ``notjira.evil.com`` does not match ``jira``.
    """
    return label in host.split(".")


def resolve_canonical_ref(raw: str) -> CanonicalRef | None:
    """Parse ``raw`` into a ``CanonicalRef``.

    Returns ``None`` if the input is not recognisable as any known identifier
    format (treat as a general full-text search term instead).

    Resolution is purely lexical — no network calls.
    """
    raw = raw.strip()
    if not raw:
        return None

    # --- bare issue key ---
    m = _BARE_ISSUE_KEY.match(raw)
    if m:
        return CanonicalRef(kind=RefKind.ISSUE_KEY, value=m.group(1).upper(), connector_hint="JIRA")

    if not _is_url(raw):
        # Google Drive file ids are 25–44 base64url characters.
        # Classify them early so the resolver tries Drive-type connectors first.
        if re.match(r"^[A-Za-z0-9_-]{25,44}$", raw):
            return CanonicalRef(kind=RefKind.DRIVE_FILE, value=raw, connector_hint="GOOGLE_DRIVE")
        # Treat remaining non-URL strings as bare external ids
        if re.match(r"^[\w\-./]{2,200}$", raw):
            return CanonicalRef(kind=RefKind.EXTERNAL_ID, value=raw)
        return None

    parsed = urlparse(raw)
    host = parsed.hostname or ""
    path = unquote(parsed.path)
    qs_raw = parsed.query

    # --- Jira ---
    if _domain_matches(host, "atlassian.net") or _has_label(host, "jira"):
        for pattern in (_JIRA_BROWSE, _JIRA_ISSUE_PATH):
            m = pattern.search(path)
            if m:
                return CanonicalRef(kind=RefKind.ISSUE_KEY, value=m.group(1).upper(), connector_hint="JIRA")
        m = _JIRA_SELECTED.search(qs_raw)
        if m:
            return CanonicalRef(kind=RefKind.ISSUE_KEY, value=m.group(1).upper(), connector_hint="JIRA")

    # --- Confluence ---
    if _has_label(host, "confluence") or _domain_matches(host, "atlassian.net"):
        m = _CONFLUENCE_PAGE_PATH.search(path)
        if m:
            return CanonicalRef(kind=RefKind.EXTERNAL_ID, value=m.group(1), connector_hint="CONFLUENCE")
        m = _CONFLUENCE_PAGE_QS.search(qs_raw)
        if m:
            return CanonicalRef(kind=RefKind.EXTERNAL_ID, value=m.group(1), connector_hint="CONFLUENCE")
        m = _CONFLUENCE_WIKI_SHORT.search(path)
        if m:
            # short link — fall back to webUrl lookup
            return CanonicalRef(kind=RefKind.WEB_URL, value=normalize_weburl(raw), connector_hint="CONFLUENCE")

    # --- Google Drive / Docs / Sheets / Slides ---
    if _domain_matches(host, "google.com") and host.split(".", 1)[0] in ("docs", "drive", "sheets", "slides"):
        m = _DRIVE_FILE_PATH.search(path)
        if m:
            return CanonicalRef(kind=RefKind.DRIVE_FILE, value=m.group(1), connector_hint="GOOGLE_DRIVE")
        m = _DRIVE_OPEN_QS.search(qs_raw)
        if m:
            return CanonicalRef(kind=RefKind.DRIVE_FILE, value=m.group(1), connector_hint="GOOGLE_DRIVE")

    # --- Slack ---
    if _domain_matches(host, "slack.com"):
        m = _SLACK_PERMALINK.search(path)
        if m:
            channel_id = m.group(1).upper()
            ts_sec = m.group(2)
            ts_micro = m.group(3) or "000000"
            ts = f"{ts_sec}.{ts_micro}"
            return CanonicalRef(
                kind=RefKind.SLACK_TS,
                value=ts,
                connector_hint="SLACK",
                channel_id=channel_id,
            )

    # --- Linear ---
    if _domain_matches(host, "linear.app"):
        m = _LINEAR_ISSUE.search(path)
        if m:
            return CanonicalRef(kind=RefKind.ISSUE_KEY, value=m.group(1).upper(), connector_hint="LINEAR")

    # --- Notion ---
    if _domain_matches(host, "notion.so") or _domain_matches(host, "notion.com"):
        m = _NOTION_UUID.search(path)
        if m:
            uid = (m.group(1) or m.group(2)).lower()
            return CanonicalRef(kind=RefKind.EXTERNAL_ID, value=uid, connector_hint="NOTION")

    # --- ServiceNow ---
    if _domain_matches(host, "service-now.com") or _domain_matches(host, "servicenow.com"):
        m = _SERVICENOW_SYS_ID.search(qs_raw)
        if m:
            return CanonicalRef(kind=RefKind.EXTERNAL_ID, value=m.group(1), connector_hint="SERVICENOW")

    # --- SharePoint ---
    if _domain_matches(host, "sharepoint.com"):
        m = _SHAREPOINT_ITEM_QS.search(qs_raw)
        if m:
            return CanonicalRef(kind=RefKind.EXTERNAL_ID, value=unquote(m.group(1)), connector_hint="SHAREPOINT")

    # --- hosts where only URL-based lookup works ---
    if host in _WEBURL_ONLY_HOSTS:
        return CanonicalRef(kind=RefKind.WEB_URL, value=normalize_weburl(raw), connector_hint=None)

    # Fall back: treat the whole URL as a webUrl lookup
    return CanonicalRef(kind=RefKind.WEB_URL, value=normalize_weburl(raw), connector_hint=None)
