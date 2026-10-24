"""Whether a search runs its exact-text pass (``run_pattern_match_with_llm_grep``).

That pass earns its cost on codes, ids, versions, file names, quoted phrases
and measured values: tokens an embedding blurs into their neighbours. On a
plain-language query it adds little: its lexical index is the one the hybrid
search's keyword leg already ranks, and it still costs an LLM call to write
its commands, plus a file scan on a small store or on an index miss.

``EXACT_TEXT_SEARCH_GATE=true`` runs it only for a query that holds such a
token, or when the agent asks for it (``exact_match``). Off by default, and
``HIGH_ACCURACY_MODE`` does not turn it on: that mode never trades the exact-text
search's time for a faster answer (``pattern_match_wait_budget``).
"""

from __future__ import annotations

import re

from app.utils.env_utils import env_bool

EXACT_TEXT_SEARCH_GATE_ENV_VAR = "EXACT_TEXT_SEARCH_GATE"

# Search queries are a sentence or two; this bounds the patterns' cost.
_MAX_READ_CHARS = 2000

_QUOTED = re.compile(
    r"\"[^\"\n]{2,}\"|“[^”\n]{2,}”|`[^`\n]{2,}`"
    # Single quotes only at word edges, so "the team's on-call" is not one.
    r"|(?<![\w'])'[^'\n]{2,}'(?![\w'])"
)

_EXACT_REQUEST = re.compile(
    r"\b(?:verbatim|exact\s+(?:match|phrase|wording|string|text|error|message|code|name)|literal(?:ly)?)\b",
    re.IGNORECASE,
)

_FILE_EXTENSIONS = (
    "py|js|ts|tsx|jsx|java|kt|go|rb|rs|c|cc|cpp|h|hpp|cs|php|swift|scala|sh|ps1|bat"
    "|ya?ml|json|toml|ini|cfg|conf|env|xml|html?|css|scss|md|rst|txt|csv|tsv|log|sql|proto"
    "|xlsx?|docx?|pptx?|pdf|odt|rtf|zip|tar|gz|tgz|jar|war|whl|dll|so|exe|dmg|apk|ipa"
    "|png|jpe?g|gif|svg|webp|mp4|mov|wav|mp3|tf|lock|gradle|dockerfile"
)

_DOTFILES = "gitignore|dockerignore|gitattributes|npmrc|nvmrc|bashrc|zshrc|profile|editorconfig|htaccess"
_EXTENSIONLESS_FILES = "Dockerfile|Makefile|Jenkinsfile|Procfile|Gemfile|Vagrantfile|Rakefile|Brewfile"

_SUB_DAY_UNITS = r"ms|µs|us|ns|secs?|seconds?|mins?|minutes?|h|hrs?|hours?"
_MEASURE_UNITS = (
    r"%|percent|[kmgtp]i?b|kbps|mbps|gbps|rps|qps|tps|rpm|iops|req/s|ops/s"
    r"|[kmgt]hz|v?cpus?|cores?|x|°[cf]|usd|eur|gbp|inr|jpy|bn|[km]"
)

_ID_KEYWORDS = (
    r"error|status|code|http|port|pr|issue|ticket|incident|case|order|invoice|build|release"
    # "no." only with its period: the bare word is English ("no 20 seats left").
    r"|sprint|ref|po|sku|account|acct|no\.|number|id"
)

_IDENTIFIER_PATTERNS = (
    # Issue and ticket keys, advisories: ABC-123, CVE-2024-1234.
    re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d+\b"),
    re.compile(r"(?<![\w&])#\d+\b"),
    # Versions and dotted numbers: v2, v1.4, 3.12, 10.0.0.1.
    re.compile(r"\bv\d+(?:\.\d+)*\b", re.IGNORECASE),
    re.compile(r"\b\d+\.\d+(?:\.\d+)*\b"),
    # File names and paths.
    # Each starts only where its run of name characters starts, not at every
    # word inside it, which made long hyphenated or dotted text quadratic.
    re.compile(rf"(?<![\w-])[\w-]+\.(?:{_FILE_EXTENSIONS})\b", re.IGNORECASE),
    re.compile(rf"(?<![\w./-])\.(?:{_FILE_EXTENSIONS}|{_DOTFILES})\b", re.IGNORECASE),
    re.compile(rf"\b(?:{_EXTENSIONLESS_FILES})\b", re.IGNORECASE),
    # A relative path of three parts or more, but not an all-digit date (10/18/2026).
    re.compile(
        r"(?<![\w/])/[\w.-]+(?:/[\w.-]+)+"
        r"|(?<![\w.-])(?!\d+/\d+/\d+\.*(?![\w/-]))[\w.-]+/[\w.-]+/[\w./-]+"
    ),
    re.compile(r"\bhttps?://\S+", re.IGNORECASE),
    # camelCase, PascalCase with an inner capital, snake_case, SCREAMING_CASE.
    re.compile(r"\b\w*[a-z][A-Z]\w*\b"),
    re.compile(r"\b[A-Za-z0-9]+(?:_[A-Za-z0-9]+)+\b"),
    # A number tied to an id-like noun: "error 503", "PR 18421", "order no. 77".
    re.compile(rf"\b(?:{_ID_KEYWORDS})\s*[#:]?\s*\d{{2,}}\b", re.IGNORECASE),
    # Long bare numbers. Four digits are mostly years, so five or more.
    re.compile(r"(?<![\d.,])\d{5,}(?![\d.,])"),
    # A measured value: "150 ms", "99%", "16 GB", "$20k". Days and longer are
    # left out: "the last 2 weeks" is a time window, not a value to match.
    re.compile(rf"(?<![\w.])\d+(?:[.,]\d+)?\s?(?:{_SUB_DAY_UNITS}|{_MEASURE_UNITS})(?![\w])", re.IGNORECASE),
    re.compile(r"[$€£¥₹]\s?\d"),
)

# Letters and digits in one token (p95, S3, SOC2, 4xx, k8s, a3f9c21), minus the
# calendar and ordinal words that look like it.
_MIXED_TOKEN = re.compile(r"\b(?=[A-Za-z]*\d)(?=\d*[A-Za-z])[A-Za-z0-9]{2,}\b")
_MIXED_NOT_IDENTIFIER = re.compile(
    r"\d+(?:st|nd|rd|th|s)|[QH]\d|FY\d{2,4}|\d{1,2}(?:am|pm)|\d{4}s",
    re.IGNORECASE,
)

# Kebab ids (us-east-1, pr-18421) share a shape with dates and "30-day".
# No lookahead for the digit: one at every start made long hyphenated text quadratic.
_KEBAB = re.compile(r"\b[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+\b")
_KEBAB_NOT_IDENTIFIER = re.compile(
    r"\d{4}-\d{2}(?:-\d{2})?|\d{1,2}-\d{1,2}-(?:\d{2}|\d{4})|\d+-(?:days?|weeks?|months?|years?|quarters?)",
    re.IGNORECASE,
)

# A two-part path (src/app_main, charts/pipeshub-ai) reads like "and/or" or
# "client/server" unless a part carries a name character prose does not use.
_TWO_PART_PATH = re.compile(r"(?<![\w./-])[\w.-]+/[\w.-]+(?![\w./-])")
_PATH_NAME_CHARS = frozenset("_.-")


def _has_mixed_token(query: str) -> bool:
    return any(
        not _MIXED_NOT_IDENTIFIER.fullmatch(token)
        for token in _MIXED_TOKEN.findall(query)
    )


def _has_kebab_identifier(query: str) -> bool:
    return any(
        any(char.isdigit() for char in token) and not _KEBAB_NOT_IDENTIFIER.fullmatch(token)
        for token in (match.group(0) for match in _KEBAB.finditer(query))
    )


def _has_two_part_path(query: str) -> bool:
    # rstrip: a sentence's closing period is not part of the path.
    return any(
        not _PATH_NAME_CHARS.isdisjoint(match.group(0).rstrip("."))
        for match in _TWO_PART_PATH.finditer(query)
    )


def has_exact_terms(query: str | None) -> bool:
    """True when ``query`` holds an identifier-like token or a quoted phrase,
    or asks for an exact match in words. A query too long to read cheaply
    counts as one, so the gate never drops exact text it did not look at."""
    if not query or not query.strip():
        return False
    if len(query) > _MAX_READ_CHARS:
        return True
    if _QUOTED.search(query) or _EXACT_REQUEST.search(query):
        return True
    if any(pattern.search(query) for pattern in _IDENTIFIER_PATTERNS):
        return True
    return _has_mixed_token(query) or _has_kebab_identifier(query) or _has_two_part_path(query)


def exact_text_search_gated() -> bool:
    """Whether the deployment runs exact-text search only where it is wanted."""
    return env_bool(EXACT_TEXT_SEARCH_GATE_ENV_VAR, False)


def exact_text_search_called_for(query: str | None, *, exact_match: bool = False) -> bool:
    """Whether a search for ``query`` needs its exact-text pass, not merely
    runs it as every search does with the gate off: the agent asked
    (``exact_match``), or the gate is on and the query holds an exact anchor."""
    return exact_match or (exact_text_search_gated() and has_exact_terms(query))


def exact_text_search_wanted(query: str | None, *, exact_match: bool = False) -> bool:
    """Whether a search for ``query`` runs its exact-text pass: always with
    the gate off; with it on, when the agent asked (``exact_match``) or the
    query holds an exact anchor."""
    return not exact_text_search_gated() or exact_text_search_called_for(query, exact_match=exact_match)


__all__ = [
    "EXACT_TEXT_SEARCH_GATE_ENV_VAR",
    "exact_text_search_called_for",
    "exact_text_search_gated",
    "exact_text_search_wanted",
    "has_exact_terms",
]
