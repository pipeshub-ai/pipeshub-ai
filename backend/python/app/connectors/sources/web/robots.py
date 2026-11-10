"""robots.txt rules as RFC 9309 matches them.

``urllib.robotparser`` applies the first rule that matches, so ``Disallow: /``
followed by ``Allow: /public`` blocks /public. RFC 9309 applies the longest
matching rule, with Allow winning a tie, and supports ``*`` and ``$``.
"""

from __future__ import annotations

import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


@dataclass
class RobotsRules:
    # (allow, pattern) pairs for the group that applies to us; empty means everything is allowed.
    rules: list[tuple[bool, str]] = field(default_factory=list)
    # Seconds the group asks between requests; not in RFC 9309, but widely sent and honoured.
    crawl_delay: float | None = None
    # Sitemap URLs listed anywhere in the file; they belong to no group.
    sitemaps: list[str] = field(default_factory=list)

    @classmethod
    def parse(cls, text: str, product_token: str) -> RobotsRules:
        """Keep the rules of the groups naming ``product_token``, or else those for ``*``.

        A group naming us applies even with no rules (RFC 9309 §2.2.1): then everything is allowed.
        """
        named: list[tuple[bool, str]] = []
        named_group = False
        anyone: list[tuple[bool, str]] = []
        delays: dict[str, float] = {}
        sitemaps: list[str] = []
        agents: list[str] = []
        in_rules = False
        token = product_token.lower()
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, value = (part.strip() for part in line.split(":", 1))
            key = key.lower()
            if key == "sitemap":
                # One malformed line (``https://[bad``) must not break every later urljoin on the list.
                if value and _parses(value):
                    sitemaps.append(value)
            elif key == "user-agent":
                if in_rules:
                    agents, in_rules = [], False
                agents.append(value.lower())
                named_group = named_group or value.lower() == token
            elif key in ("allow", "disallow"):
                in_rules = True
                if not value:
                    continue  # "Disallow:" with no path allows everything
                rule = (key == "allow", _decode_unreserved(value))
                if token in agents:
                    named.append(rule)
                if "*" in agents:
                    anyone.append(rule)
            elif key == "crawl-delay":
                in_rules = True
                delay = _seconds(value)
                for agent in agents:
                    if delay is not None and agent in (token, "*"):
                        delays.setdefault(agent, delay)
        return cls(
            named if named_group else anyone,
            crawl_delay=delays.get(token) if named_group else delays.get("*"),
            sitemaps=sitemaps,
        )

    def allows(self, url: str) -> bool:
        parsed = urlparse(url)
        target = _decode_unreserved((parsed.path or "/") + (f"?{parsed.query}" if parsed.query else ""))
        if target == "/robots.txt":
            return True
        best: tuple[int, bool] | None = None
        for allow, pattern in self.rules:
            if _matches(pattern, target):
                candidate = (len(pattern), allow)
                if best is None or candidate > best:  # longer wins; on equal length, Allow (True) wins
                    best = candidate
        return best is None or best[1]


def _parses(url: str) -> bool:
    try:
        urlparse(url)
    except ValueError:
        return False
    return True


def _seconds(value: str) -> float | None:
    try:
        seconds = float(value)
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


class RobotsCache:
    """Each site's robots.txt rules, shared by every crawl in the process.

    RFC 9309 §2.4 lets a crawler keep a robots.txt for up to 24 hours. A site that can't be read
    keeps its last good rules for up to 30 days (as major search engines do) rather than being
    left out of a sync for one bad minute. robots.txt is public, so sharing it across connectors
    shares nothing private.
    """

    FRESH_SECONDS = 24 * 3600
    STALE_SECONDS = 30 * 24 * 3600

    def __init__(self, *, now: Callable[[], float] = time.monotonic, max_sites: int = 2000) -> None:
        self._now = now
        self._max_sites = max_sites
        # origin -> (rules, read at)
        self._good: OrderedDict[str, tuple[RobotsRules, float]] = OrderedDict()

    async def rules(self, origin: str, read: Callable[[], Awaitable[RobotsRules | None]]) -> RobotsRules | None:
        """Fresh cached rules, else ``read()``'s, else stale cached ones; None when there are none to use."""
        cached = self._good.get(origin)
        now = self._now()
        if cached is not None and now - cached[1] < self.FRESH_SECONDS:
            self._good.move_to_end(origin)
            return cached[0]
        fresh = await read()
        if fresh is not None:
            self._good[origin] = (fresh, now)
            self._good.move_to_end(origin)
            while len(self._good) > self._max_sites:
                self._good.popitem(last=False)
            return fresh
        if cached is not None and now - cached[1] < self.STALE_SECONDS:
            return cached[0]
        return None

    def clear(self) -> None:
        self._good.clear()


_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


def _decode_unreserved(text: str) -> str:
    """RFC 9309 §2.2.2: %62 and b are the same character; reserved escapes such as %2F stay encoded."""
    def _one(match: re.Match[str]) -> str:
        char = chr(int(match.group(1), 16))
        return char if char in _UNRESERVED else "%" + match.group(1).upper()
    return re.sub(r"%([0-9A-Fa-f]{2})", _one, text)


def _matches(pattern: str, target: str) -> bool:
    """Whether ``pattern`` ("*" is any run of characters, a final "$" anchors the end) matches ``target``
    from its start. A single left-to-right scan: the site writes both the pattern and the paths, and a
    backtracking regex on "/*a*a*a*...b" can run for minutes."""
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    return _glob(body if anchored else body + "*", target)


def _glob(pattern: str, text: str) -> bool:
    """Whole-string match with "*" wildcards, in O(len(pattern) * len(text)) at worst."""
    p = t = 0
    star, resume = -1, 0
    while t < len(text):
        if p < len(pattern) and pattern[p] == "*":
            star, resume = p, t
            p += 1
        elif p < len(pattern) and pattern[p] == text[t]:
            p += 1
            t += 1
        elif star != -1:
            p, resume = star + 1, resume + 1
            t = resume
        else:
            return False
    while p < len(pattern) and pattern[p] == "*":
        p += 1
    return p == len(pattern)


# One per process: every web connector reads robots.txt through it.
shared_robots_cache = RobotsCache()
