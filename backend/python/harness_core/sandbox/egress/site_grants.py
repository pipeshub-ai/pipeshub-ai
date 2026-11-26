"""Websites a person allowed for one run (WP-63, SEC-A-01, the site card): a website held after the run read private
data, released for that run's sandbox and that host.

The worker asks the person once per run and site (``pipeshub@egress/site_upload``) and, when they allow it, registers
a grant here (``POST /v1/site-grants``): this sandbox, this host, this run. From then on the proxy lets that sandbox
reach that host as its entry allows; until then each request that could carry what the run read is refused
(``site_upload_held``, ``site_request_held``). A grant names
the run its sandbox is bound to now and goes with the binding (released, or bound again for another run), so it never
outlives the run the person allowed it for.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from harness_core.sandbox.egress.policy import normalize_host
from harness_core.sandbox.egress.sites import SITE_PORT, host_covers
from harness_core.sandbox.egress.volume import run_of

if TYPE_CHECKING:
    from harness_core.sandbox.egress.types import EgressBinding

MAX_GRANTS_PER_SANDBOX: Final = 32


class SiteGrantInvalid(ValueError):
    """A grant the proxy will not hold: the sandbox serves another run, or holds no held entry for the host."""


class SiteGrants:
    """Released holds, by sandbox: the hosts its run may write to now. In memory: a proxy that restarts holds the
    site again until the worker grants it anew (the next turn's binding carries the person's decision itself)."""

    def __init__(self) -> None:
        self._grants: dict[str, set[str]] = {}

    def issue(self, binding: EgressBinding, *, host: str, run_id: str, run_label: str = "runId") -> None:
        name = normalize_host(host)
        serving = binding.labels.get(run_label) or run_of(binding.labels)
        if not run_id or serving != run_id:
            raise SiteGrantInvalid("the sandbox does not serve that run")
        held = [
            d for d in binding.policy.destinations
            if d.site is not None and d.site.held and SITE_PORT in d.ports
            and (d.host == name or host_covers(d.host, name))
        ]
        if not held:
            raise SiteGrantInvalid("the sandbox holds no held website for that host")
        granted = self._grants.setdefault(binding.sandbox_id, set())
        if len(granted) >= MAX_GRANTS_PER_SANDBOX and name not in granted:
            raise SiteGrantInvalid("too many released websites for this sandbox")
        granted.add(name)

    def released(self, binding: EgressBinding, host: str) -> bool:
        return normalize_host(host) in self._grants.get(binding.sandbox_id, ())

    def drop(self, sandbox_id: str) -> None:
        _ = self._grants.pop(sandbox_id, None)


__all__ = ["SiteGrantInvalid", "SiteGrants"]
