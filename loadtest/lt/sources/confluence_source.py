"""Confluence load source — for finding the connector service's saturation point.

Unlike the other sources this one is **read-only against a live site** and
seeds nothing: `seed()` only resolves which spaces each unit will sync. It is
marked non-deterministic because the content is not ours to freeze — pages
change, and Atlassian rate-limits, so two runs are not strictly comparable.
That is acceptable here: the question is "at what N does the connector process
peg a core", which is answered by the CPU curve rather than by throughput.

Two unit layouts:

  same-space (default)  every instance syncs the same space, so all N units do
                        identical work and the CPU curve is clean. Costs N x
                        the API calls against one space.
  per-space             instance i syncs space i. Spreads API load, but real
                        spaces differ wildly in size (158 pages vs 3 here), so
                        the units are not comparable to each other.
"""

from __future__ import annotations

import os
from typing import Any

import requests

from .base import LoadSource, Unit, registry


@registry.register
class ConfluenceSource(LoadSource):
    name = "confluence"
    connector_type = "Confluence"
    # Live SaaS: content drifts and the API throttles, so throughput numbers
    # from two runs are not A/B-comparable. `compare` refuses a verdict on
    # these, which is correct.
    deterministic = False
    compose_file = None
    auth_type = "API_TOKEN"
    # The connector declares TEAM only (confluence_cloud/connector.py:218).
    scope = "team"

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        opts = self.options
        self.base_url = (opts.get("base_url") or os.getenv("LT_CONFLUENCE_BASE_URL", "")).rstrip("/")
        self.email = opts.get("email") or os.getenv("LT_CONFLUENCE_EMAIL", "")
        self.api_token = opts.get("api_token") or os.getenv("LT_CONFLUENCE_API_TOKEN", "")
        #: "same-space", "per-space", or "all-spaces" (no space filter at all)
        self.layout = opts.get("layout", "same-space")
        #: space key(s) to sync. For same-space, the first is used by every unit.
        raw = opts.get("spaces") or os.getenv("LT_CONFLUENCE_SPACES", "")
        self.spaces: list[str] = [s.strip() for s in raw.split(",") if s.strip()] if isinstance(raw, str) else list(raw)

    # -- helpers -----------------------------------------------------------

    def _api(self, path: str, params: dict | None = None, timeout: int = 30) -> requests.Response:
        return requests.get(
            f"{self.base_url}{path}",
            params=params or {},
            auth=(self.email, self.api_token),
            headers={"Accept": "application/json"},
            timeout=timeout,
        )

    def page_count(self, space_key: str) -> int:
        """Pages in a space, for sizing the run and for the expected record count."""
        resp = self._api(
            "/wiki/rest/api/search",
            {"cql": f"type=page and space={space_key}", "limit": 1},
        )
        if resp.status_code != 200:
            return 0
        return int(resp.json().get("totalSize", 0))

    def all_space_keys(self) -> list[str]:
        """Every space the account can see."""
        resp = self._api("/wiki/rest/api/space", {"limit": 200})
        if resp.status_code != 200:
            return []
        return [s["key"] for s in resp.json().get("results", []) if s.get("key")]

    def total_page_count(self) -> int:
        """Pages across every visible space — the unfiltered corpus size."""
        return sum(self.page_count(key) for key in self.all_space_keys())

    # -- lifecycle ---------------------------------------------------------

    def preflight(self) -> list[str]:
        missing = [
            name
            for name, value in (
                ("LT_CONFLUENCE_BASE_URL", self.base_url),
                ("LT_CONFLUENCE_EMAIL", self.email),
                ("LT_CONFLUENCE_API_TOKEN", self.api_token),
            )
            if not value
        ]
        if missing:
            return [f"missing Confluence settings: {', '.join(missing)} (set them in loadtest/.env)"]
        if not self.spaces and self.layout != "all-spaces":
            return ["no spaces selected: set LT_CONFLUENCE_SPACES (comma-separated space keys)"]

        try:
            resp = self._api("/wiki/rest/api/user/current")
        except requests.RequestException as exc:
            return [f"Confluence unreachable at {self.base_url}: {exc}"]
        if resp.status_code == 401:
            return ["Confluence rejected the credentials (401) — check email and API token"]
        if resp.status_code != 200:
            return [f"Confluence auth check returned HTTP {resp.status_code}"]

        problems = []
        for key in self.spaces:
            if self.page_count(key) == 0:
                problems.append(f"space {key!r} has no pages visible to this account")
        return problems

    def seed(self, units: int, scale: int, seed: int) -> list[Unit]:
        """Resolve spaces per unit. Creates nothing — this is a live site.

        `scale` is ignored: the record count is whatever the space holds. It is
        reported back as expected_records so the runner knows when a unit is
        done.
        """
        made: list[Unit] = []

        if self.layout == "all-spaces":
            # No space filter, so the connector walks the whole site. Sending an
            # empty `sync.values` is what "unfiltered" means to the filters-sync
            # endpoint; MANUAL_INDEX_OFF still comes from Unit.filters_payload,
            # so the run stays connector-only.
            total = self.total_page_count()
            return [
                Unit(
                    index=index,
                    expected_records=total,
                    label="all-spaces",
                    auth={
                        "baseUrl": self.base_url,
                        "email": self.email,
                        "apiToken": self.api_token,
                    },
                    sync_filters={},
                )
                for index in range(units)
            ]

        for index in range(units):
            if self.layout == "per-space":
                if index >= len(self.spaces):
                    raise RuntimeError(
                        f"per-space layout needs >= {units} spaces, got {len(self.spaces)}"
                    )
                key = self.spaces[index]
            else:
                key = self.spaces[0]

            made.append(
                Unit(
                    index=index,
                    expected_records=self.page_count(key),
                    label=key,
                    auth={
                        "baseUrl": self.base_url,
                        "email": self.email,
                        "apiToken": self.api_token,
                    },
                    sync_filters={
                        "space_keys": {
                            "value": [key],
                            "operator": "in",
                            "type": "list",
                        }
                    },
                )
            )
        return made

    def teardown(self, units: list[Unit]) -> None:
        """Nothing to undo: this source never wrote to Confluence."""

    def describe(self) -> dict[str, Any]:
        described = super().describe()
        described["layout"] = self.layout
        described["spaces"] = self.spaces
        described["base_url"] = self.base_url
        return described
