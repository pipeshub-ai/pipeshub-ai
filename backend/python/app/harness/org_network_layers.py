"""Where a run's allowed websites and git hosts come from beyond the release's (WP-63).

The org's live layer carries the ``websites`` and ``repositories`` members as Node holds them: the deployment's cap,
and an org's and each agent's lists where something wrote them. This build writes no org or agent list, so without an
edition's layers (``edition_config.org_network_layers``) a run reaches no website, and clones only from the release's
public presets on a run with web access, each held by the deployment's cap (ADR-REPO-01). Whatever the layers answer
is checked again before it is used (``app.harness.websites.site_destinations``, ``app.harness.repositories.host_ok``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue


class OrgNetworkLayers(Protocol):
    def sites(self, websites: Mapping[str, JsonValue], agent_key: str | None) -> list[Mapping[str, JsonValue]]:
        """The run's sites from the live layer's ``websites`` member; ``agent_key`` is the run's agent, if any."""
        ...

    def repository_hosts(
        self, repositories: Mapping[str, JsonValue], agent_key: str | None, default: JsonValue | None
    ) -> list[Mapping[str, JsonValue]]:
        """The run's git hosts from the live layer's ``repositories`` member. ``default``: the section an org that
        saved none stands for (the release's presets on a run with web access; ``None`` without)."""
        ...


def org_network_layers() -> OrgNetworkLayers | None:
    """The edition's layers, read at call time so an edition extension that sets them is honoured; none here."""
    from app import edition_config

    return cast("OrgNetworkLayers | None", getattr(edition_config, "org_network_layers", None))


__all__ = ["OrgNetworkLayers", "org_network_layers"]
