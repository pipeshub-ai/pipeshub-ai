# pyright: ignore-file

"""Builds the small static site the Web connector crawls.

The site is served by an nginx container in the integration stack. Crawling a
real site would tie the suite to that site's uptime and wording, and would send
traffic to someone else's server on every CI run.

The pages form a deliberate link tree so crawl depth can be tested:

    index.html                     depth 0
      -> guides/setup.html         depth 1
      -> guides/billing.html       depth 1
           -> guides/deep.html     depth 2   (linked only from setup.html)

A crawl limited to depth 1 must reach the two guides and not `deep.html`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List

# path -> (title, body, [links])
PageSpec = Dict[str, tuple]

SITE: PageSpec = {
    "index.html": (
        "PipesHub test site",
        "Landing page for the Web connector integration tests.",
        ["guides/setup.html", "guides/billing.html"],
    ),
    "guides/setup.html": (
        "Setup guide",
        "How to set up a new workspace from scratch.",
        ["deep.html"],
    ),
    "guides/billing.html": (
        "Billing guide",
        "Answers to common billing questions.",
        [],
    ),
    "guides/deep.html": (
        "Deep page",
        "Only reachable from the setup guide, two hops from the landing page.",
        [],
    ),
}

# Titles at each crawl depth from index.html.
DEPTH_0_TITLES = ["PipesHub test site"]
DEPTH_1_TITLES = ["Setup guide", "Billing guide"]
DEPTH_2_TITLES = ["Deep page"]


class WebSourceHelper:
    """Creates and updates the static site a connector test crawls."""

    def __init__(self, host_dir: str) -> None:
        self.root = Path(host_dir)

    def ensure_root(self) -> None:
        """Create the document root and prove it is writable.

        nginx serves as an unprivileged user, so the root has to be traversable
        by it. A restrictive umask otherwise produces a 403 that reads like a
        missing page.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o755)
        probe = self.root / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()

    @staticmethod
    def _html(title: str, body: str, links: List[str]) -> str:
        anchors = "\n".join(
            f'    <li><a href="{href}">{href}</a></li>' for href in links
        )
        link_block = f"  <ul>\n{anchors}\n  </ul>\n" if links else ""
        return (
            "<!doctype html>\n"
            '<html lang="en">\n'
            "<head>\n"
            '  <meta charset="utf-8">\n'
            f"  <title>{title}</title>\n"
            "</head>\n"
            "<body>\n"
            f"  <h1>{title}</h1>\n"
            f"  <p>{body}</p>\n"
            f"{link_block}"
            "</body>\n"
            "</html>\n"
        )

    def write_site(self, site: PageSpec | None = None) -> int:
        pages = site if site is not None else SITE
        for rel_path, (title, body, links) in pages.items():
            target = self.root / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            os.chmod(target.parent, 0o755)
            target.write_text(self._html(title, body, links), encoding="utf-8")
            os.chmod(target, 0o644)  # readable by the nginx user
        return len(pages)

    def write_page(self, rel_path: str, title: str, body: str, links: List[str]) -> None:
        target = self.root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(target.parent, 0o755)
        target.write_text(self._html(title, body, links), encoding="utf-8")
        os.chmod(target, 0o644)

    def list_objects(self, resource_name: str | None = None) -> List[str]:
        del resource_name
        return sorted(
            str(p.relative_to(self.root))
            for p in self.root.rglob("*.html")
            if p.is_file()
        )

    def clear_objects(self, resource_name: str | None = None) -> None:
        """Remove the pages, leaving the document root in place.

        The directory is the bind-mount target and has to survive teardown.
        """
        del resource_name
        for path in sorted(self.root.rglob("*"), reverse=True):
            if path.name == ".gitkeep":
                continue
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
