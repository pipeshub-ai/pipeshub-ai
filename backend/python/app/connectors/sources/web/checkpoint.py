"""Where an unfinished crawl stopped, so the next sync carries on from there instead of from the start page.

Saved with the connector's sync point as compressed text, which every graph store can hold as a
plain property. A checkpoint only applies to the settings it was taken under, and only for a week:
after that the site has moved on and a fresh crawl is cheaper than a stale one.
"""

import base64
import binascii
import hashlib
import json
import zlib
from collections.abc import Mapping
from dataclasses import dataclass, field

MAX_AGE_MS = 7 * 24 * 3600 * 1000
# Bounds the sync point's size: a crawl larger than this starts again from its start page.
MAX_URLS = 50_000


def settings_fingerprint(settings: Mapping[str, object]) -> str:
    """Identifies the crawl settings a checkpoint belongs to."""
    return hashlib.sha256(json.dumps(settings, sort_keys=True, default=str).encode()).hexdigest()[:16]


@dataclass
class CrawlCheckpoint:
    fingerprint: str
    saved_at_ms: int
    # Normalised URLs this crawl already settled, and those of them it kept as pages.
    visited: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    # (url, depth) still to fetch.
    queue: list[tuple[str, int]] = field(default_factory=list)

    def encode(self) -> str:
        """Empty over ``MAX_URLS``: a cut-down queue would let a resumed crawl finish as complete
        without the dropped pages, and prune what only they still linked to."""
        if max(len(self.visited), len(self.kept), len(self.queue)) > MAX_URLS:
            return ""
        data = {
            "fingerprint": self.fingerprint,
            "saved_at_ms": self.saved_at_ms,
            "visited": self.visited,
            "kept": self.kept,
            "queue": [[url, depth] for url, depth in self.queue],
        }
        return base64.b64encode(zlib.compress(json.dumps(data).encode(), 6)).decode("ascii")

    @classmethod
    def decode(cls, text: object, *, fingerprint: str, now_ms: int) -> "CrawlCheckpoint | None":
        """The checkpoint in ``text``, if it is one for these settings and recent enough to use."""
        if not isinstance(text, str) or not text:
            return None
        try:
            data = json.loads(zlib.decompress(base64.b64decode(text, validate=True)))
            checkpoint = cls(
                fingerprint=str(data["fingerprint"]),
                saved_at_ms=int(data["saved_at_ms"]),
                visited=[str(u) for u in data["visited"]],
                kept=[str(u) for u in data["kept"]],
                queue=[(str(url), int(depth)) for url, depth in data["queue"]],
            )
        except (binascii.Error, zlib.error, ValueError, KeyError, TypeError):
            return None
        if checkpoint.fingerprint != fingerprint or now_ms - checkpoint.saved_at_ms > MAX_AGE_MS:
            return None
        return checkpoint
