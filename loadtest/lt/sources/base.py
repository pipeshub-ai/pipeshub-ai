"""The load-source contract.

A load source owns everything connector-specific: standing up its backing
service, seeding a reproducible corpus, and producing the connector config the
Pipeshub API needs. The runner knows none of that — adding a connector to the
harness is one new file here plus (optionally) one compose file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: `enable_manual_sync=true` short-circuits every indexing filter to False
#: (filters.py:865), so records land in the graph with AUTO_INDEX_OFF and are
#: never published to indexing. Every load source must send this — it is what
#: keeps a run connector-only. FilterCollection.from_dict rejects any filter
#: missing `operator`/`type`, hence the full triple rather than a bare bool.
MANUAL_INDEX_OFF = {"enable_manual_sync": {"value": True, "operator": "is", "type": "boolean"}}


@dataclass
class Unit:
    """One connector instance's worth of data.

    A scenario with `units: 4` seeds four disjoint corpora and creates four
    connectors, which is how concurrent-sync load is generated.

    `auth` is sent as `config.auth` at create time; the filters go through the
    separate filters-sync endpoint afterwards, because the backend rejects
    filter updates while a connector is enabled.

    Filters are split by category because the backend keys on it: the
    filters-sync handler copies only the `sync` and `indexing` keys
    (router.py:3900), and the connector reads them back from
    `filters.<category>.values` (filters.py:1012). A flat dict is accepted with
    a success response and then silently dropped.
    """

    index: int
    expected_records: int
    label: str
    auth: dict[str, Any] = field(default_factory=dict)
    sync_filters: dict[str, Any] = field(default_factory=dict)
    indexing_filters: dict[str, Any] = field(default_factory=dict)
    #: connector-level sync settings (`add_sync_custom_field`, e.g. the Web
    #: connector's url/depth/max_pages). Sent as the `sync` half of the same
    #: filters-sync call, and merged rather than category-nested.
    sync_config: dict[str, Any] = field(default_factory=dict)

    def filters_payload(self) -> dict[str, Any]:
        """The nested body the filters-sync endpoint actually reads."""
        return {
            "sync": {"values": dict(self.sync_filters)},
            "indexing": {"values": {**self.indexing_filters, **MANUAL_INDEX_OFF}},
        }


class LoadSource:
    """Base class for load sources. Subclasses override the marked methods."""

    #: registry key, used in scenario YAML as `source: <name>`
    name: str = ""
    #: `connectorType` accepted by POST /api/v1/connectors/
    connector_type: str = ""
    #: False for SaaS-backed sources whose numbers are not run-to-run comparable
    deterministic: bool = False
    #: compose file under loadtest/compose/, or None if no backing service is needed
    compose_file: str | None = None
    #: connector scope used when creating instances
    scope: str = "personal"
    #: auth type passed to the create-connector API, if the connector needs one
    auth_type: str | None = None

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        self.options: dict[str, Any] = options or {}

    # -- lifecycle ---------------------------------------------------------

    def preflight(self) -> list[str]:
        """Return a list of problems that would stop a run; empty means ready."""
        return []

    def seed(self, units: int, scale: int, seed: int) -> list[Unit]:
        """Create `units` disjoint corpora of `scale` records each.

        Must be deterministic for a given (units, scale, seed) when
        `deterministic` is True: same record count, same names, same sizes.
        """
        raise NotImplementedError

    def teardown(self, units: list[Unit]) -> None:
        """Remove seeded data. Best-effort; must not raise."""

    # -- description -------------------------------------------------------

    def describe(self) -> dict[str, Any]:
        """Recorded verbatim in result.json so a run can be traced back."""
        return {
            "name": self.name,
            "connector_type": self.connector_type,
            "deterministic": self.deterministic,
            "options": {
                k: v
                for k, v in self.options.items()
                # `api_token` and `email` contain neither "secret" nor "key",
                # so a narrower filter writes real credentials into result.json.
                if not any(
                    m in k.lower()
                    for m in ("secret", "key", "token", "password", "passwd", "email", "credential")
                )
            },
        }


@dataclass
class Registry:
    sources: dict[str, type[LoadSource]] = field(default_factory=dict)

    def register(self, cls: type[LoadSource]) -> type[LoadSource]:
        self.sources[cls.name] = cls
        return cls

    def get(self, name: str) -> type[LoadSource]:
        try:
            return self.sources[name]
        except KeyError:
            known = ", ".join(sorted(self.sources)) or "none"
            raise KeyError(f"unknown load source `{name}` (known: {known})") from None


registry = Registry()
