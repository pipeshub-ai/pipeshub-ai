# pyright: ignore-file

"""Writes the RSS feed the RSS connector syncs from.

The feed is served by an nginx container in the integration stack rather than
fetched from a real site. A public feed would make this suite fail whenever that
site changed its content or went down, which is noise rather than signal about
the connector.

The feed is written on the host side of a bind mount; nginx serves the same
bytes to the connector.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Sequence, Tuple

# (title, description) — the guid is derived from the title, so re-writing a
# feed with the same titles produces the same guids, which is what the
# deduplication case depends on.
Article = Tuple[str, str]

BASE_ARTICLES: List[Article] = [
    ("Release notes 2026.09", "What changed in the September release."),
    ("Scaling the indexer", "How the indexing pipeline handles large corpora."),
    ("Connector roadmap", "Which sources are planned next."),
]


class RssSourceHelper:
    """Creates and updates the feed a connector test syncs from."""

    def __init__(self, host_dir: str, feed_name: str = "feed.xml") -> None:
        self.root = Path(host_dir)
        self.feed_name = feed_name

    @property
    def feed_path(self) -> Path:
        return self.root / self.feed_name

    def ensure_root(self) -> None:
        """Create the document root and prove it is writable.

        A bind-mount target created by Docker is owned by root. Failing here
        with a clear reason beats failing later inside a sync that fetched a
        404.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        # nginx serves as its own unprivileged user, so the document root has to
        # be traversable by it. A restrictive umask on the runner otherwise
        # produces a 403 that looks like a missing feed.
        os.chmod(self.root, 0o755)
        probe = self.root / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()

    @staticmethod
    def _item(title: str, description: str, published: datetime) -> str:
        # The guid is stable for a given title so that re-publishing an
        # unchanged feed does not look like new articles.
        guid = title.lower().replace(" ", "-").replace(".", "")
        return (
            "    <item>\n"
            f"      <title>{title}</title>\n"
            f"      <description>{description}</description>\n"
            f"      <link>https://example.invalid/{guid}</link>\n"
            f'      <guid isPermaLink="false">{guid}</guid>\n'
            f"      <pubDate>{published.strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate>\n"
            "    </item>\n"
        )

    def write_feed(self, articles: Sequence[Article]) -> int:
        """Write the whole feed, replacing whatever was there."""
        now = datetime.now(timezone.utc)
        items = "".join(
            self._item(title, description, now - timedelta(hours=index))
            for index, (title, description) in enumerate(articles)
        )
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<rss version="2.0">\n'
            "  <channel>\n"
            "    <title>PipesHub connector test feed</title>\n"
            "    <link>https://example.invalid/</link>\n"
            "    <description>Fixture feed for the RSS connector integration tests.</description>\n"
            f"{items}"
            "  </channel>\n"
            "</rss>\n"
        )
        self.root.mkdir(parents=True, exist_ok=True)
        self.feed_path.write_text(xml, encoding="utf-8")
        os.chmod(self.feed_path, 0o644)  # readable by the nginx user
        return len(articles)

    def article_titles(self) -> List[str]:
        """Titles currently in the feed, read back from the file."""
        if not self.feed_path.exists():
            return []
        text = self.feed_path.read_text(encoding="utf-8")
        titles: List[str] = []
        for chunk in text.split("<item>")[1:]:
            start = chunk.find("<title>") + len("<title>")
            end = chunk.find("</title>")
            if start > len("<title>") - 1 and end > start:
                titles.append(chunk[start:end])
        return titles

    def clear_objects(self, resource_name: str | None = None) -> None:
        """Remove the feed, leaving the document root in place.

        Named for the protocol the shared destructor expects. The directory is
        the bind-mount target and has to survive teardown.
        """
        del resource_name
        if self.feed_path.exists():
            self.feed_path.unlink()
