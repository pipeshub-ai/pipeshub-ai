"""Client for the ``web-fixtures`` service, the website the Web and RSS connectors sync from.

The service (``integration-tests/web_fixtures/server.py``) serves the seed files
under ``integration-tests/web_fixtures/seed``. Tests change a page or a feed
through its control API rather than a shared directory, so nothing depends on
the runner and the container agreeing on file ownership.

The connector reaches the service by its compose name; the test process uses
the published port. Both default to the integration compose files' values.
"""

from __future__ import annotations

import os

import requests

CONTROL = "/__fixtures__"


class WebFixtures:
    def __init__(
        self,
        test_url: str | None = None,
        connector_url: str | None = None,
        timeout: float = 10,
    ) -> None:
        self.test_url = (test_url or os.getenv("WEB_FIXTURES_TEST_URL", "http://localhost:8089")).rstrip("/")
        self.connector_url = (
            connector_url or os.getenv("WEB_FIXTURES_CONNECTOR_URL", "http://web-fixtures:8080")
        ).rstrip("/")
        self.timeout = timeout

    def url_for_connector(self, path: str) -> str:
        """The URL of ``path`` as the connector, inside the compose network, sees it."""
        return f"{self.connector_url}/{path.lstrip('/')}"

    def check_available(self) -> None:
        requests.get(f"{self.test_url}{CONTROL}/health", timeout=self.timeout).raise_for_status()

    def get(self, path: str) -> str:
        response = requests.get(f"{self.test_url}/{path.lstrip('/')}", timeout=self.timeout)
        response.raise_for_status()
        return response.text

    def put(self, path: str, content: str, content_type: str | None = None) -> None:
        headers = {"Content-Type": content_type} if content_type else {}
        requests.put(
            f"{self.test_url}{CONTROL}/files/{path.lstrip('/')}",
            data=content.encode("utf-8"),
            headers=headers,
            timeout=self.timeout,
        ).raise_for_status()

    def delete(self, path: str) -> None:
        requests.delete(
            f"{self.test_url}{CONTROL}/files/{path.lstrip('/')}", timeout=self.timeout
        ).raise_for_status()

    def reset(self) -> None:
        requests.post(f"{self.test_url}{CONTROL}/reset", timeout=self.timeout).raise_for_status()

    def clear_objects(self, resource_name: str) -> None:
        """What ``connector_lifecycle.destructor`` calls to clean up a source: serve the seed again."""
        self.reset()


def html_page(title: str, body: str, links: list[tuple[str, str]] | None = None) -> str:
    """A page in the same shape as the seed pages: a <title>, an <h1>, a paragraph and links."""
    items = "".join(f'<li><a href="{href}">{text}</a></li>' for href, text in links or [])
    nav = f"<ul>{items}</ul>" if items else ""
    return (
        f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{title}</title></head>'
        f"<body><h1>{title}</h1><p>{body}</p>{nav}</body></html>"
    )
