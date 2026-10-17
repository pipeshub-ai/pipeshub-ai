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
from typing import Any

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
        """Serve the seed files again and drop every fault."""
        requests.post(f"{self.test_url}{CONTROL}/reset", timeout=self.timeout).raise_for_status()

    def add_fault(
        self,
        path: str,
        *,
        status: int | None = None,
        retry_after: int | None = None,
        delay: float = 0.0,
        truncate: bool = False,
        partial: bool = False,
        times: int | None = None,
    ) -> None:
        """Make ``path`` misbehave for its next ``times`` requests (None: until removed).

        ``status`` answers with that HTTP error (and ``Retry-After`` when given),
        ``delay`` holds every answer back that many seconds, ``truncate`` drops the
        connection halfway through the page, and ``partial`` serves half the page
        as if it were whole. Setting a fault on a path replaces the one there.
        """
        spec: dict[str, Any] = {"delay": delay, "truncate": truncate, "partial": partial, "times": times}
        if status is not None:
            spec["status"] = status
        if retry_after is not None:
            spec["retry_after"] = retry_after
        response = requests.put(
            f"{self.test_url}{CONTROL}/faults/{path.lstrip('/')}", json=spec, timeout=self.timeout
        )
        if response.status_code == 400:
            raise ValueError(f"web-fixtures rejected the fault for {path}: {response.text}")
        response.raise_for_status()

    def remove_fault(self, path: str) -> None:
        response = requests.delete(f"{self.test_url}{CONTROL}/faults/{path.lstrip('/')}", timeout=self.timeout)
        if response.status_code != 404:
            response.raise_for_status()

    def fault_hits(self, path: str) -> int:
        """How many requests the fault on ``path`` has answered; 0 when there is none.

        A test asserts on this so it cannot pass because the connector never
        asked for the page, rather than because it got past the fault.
        """
        response = requests.get(f"{self.test_url}{CONTROL}/faults/{path.lstrip('/')}", timeout=self.timeout)
        if response.status_code == 404:
            return 0
        response.raise_for_status()
        return int(response.json()["served"])

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
