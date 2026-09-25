"""robots.txt rules as RFC 9309 matches them.

``urllib.robotparser`` applies the first rule that matches, so ``Disallow: /``
followed by ``Allow: /public`` blocks /public. RFC 9309 applies the longest
matching rule, with Allow winning a tie, and supports ``*`` and ``$``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse


@dataclass
class RobotsRules:
    # (allow, pattern) pairs for the group that applies to us; empty means everything is allowed.
    rules: list[tuple[bool, str]] = field(default_factory=list)

    @classmethod
    def parse(cls, text: str, product_token: str) -> RobotsRules:
        """Keep the rules of the groups naming ``product_token``, or else those for ``*``."""
        named: list[tuple[bool, str]] = []
        anyone: list[tuple[bool, str]] = []
        agents: list[str] = []
        in_rules = False
        token = product_token.lower()
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, value = (part.strip() for part in line.split(":", 1))
            key = key.lower()
            if key == "user-agent":
                if in_rules:
                    agents, in_rules = [], False
                agents.append(value.lower())
            elif key in ("allow", "disallow"):
                in_rules = True
                if not value:
                    continue  # "Disallow:" with no path allows everything
                rule = (key == "allow", value)
                if token in agents:
                    named.append(rule)
                if "*" in agents:
                    anyone.append(rule)
        return cls(named or anyone)

    def allows(self, url: str) -> bool:
        parsed = urlparse(url)
        target = (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")
        if target == "/robots.txt":
            return True
        best: tuple[int, bool] | None = None
        for allow, pattern in self.rules:
            if _matches(pattern, target):
                candidate = (len(pattern), allow)
                if best is None or candidate > best:  # longer wins; on equal length, Allow (True) wins
                    best = candidate
        return best is None or best[1]


def _matches(pattern: str, target: str) -> bool:
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    regex = ".*".join(re.escape(part) for part in body.split("*"))
    return re.match(regex + ("$" if anchored else ""), target) is not None
