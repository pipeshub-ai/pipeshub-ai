"""Small bridge networks for Docker test rigs on a shared daemon.

Docker gives a network created without IPAM a whole /16 or /20 from its default pools, of which a daemon has
about 30; a few parallel lanes plus other users exhaust them ("all predefined address pools have been fully
subnetted"). Rigs take a /28 from their own range instead, next to the broker's sandbox pools (10.213/16 in
production and the Docker lanes, 10.214/16 in E1's G4 stack), retrying when another network holds it, by the
broker's own rule (``is_pool_overlap``).
"""

from __future__ import annotations

import ipaddress
import itertools
import random
from typing import TYPE_CHECKING, Any

from app.harness.sandbox.docker_egress import is_pool_overlap

if TYPE_CHECKING:
    from collections.abc import Iterable

RIG_POOL = ipaddress.ip_network("10.215.0.0/16")
RIG_PREFIX = 28
_ATTEMPTS = 32


def create_on_free_subnet(
    client: Any,  # noqa: ANN401 - docker.DockerClient
    name: str,
    subnets: Iterable[str],
    *,
    labels: dict[str, str],
    internal: bool = False,
    options: dict[str, str] | None = None,
    attempts: int = _ATTEMPTS,
) -> str:
    """Creates ``name`` on the first of ``subnets`` (drawn lazily) that no other network holds; returns it."""
    from docker.types import IPAMConfig, IPAMPool

    for subnet in itertools.islice(subnets, attempts):
        gateway = str(next(ipaddress.ip_network(subnet).hosts()))
        try:
            client.api.create_network(
                name,
                driver="bridge",
                internal=internal,
                labels=labels,
                options=options,
                ipam=IPAMConfig(pool_configs=[IPAMPool(subnet=subnet, gateway=gateway)]),
            )
        except Exception as exc:
            if not is_pool_overlap(exc):
                raise
            continue
        return subnet
    raise RuntimeError(f"no free subnet for {name} after {attempts} tries")


def create_rig_network(
    client: Any,  # noqa: ANN401 - docker.DockerClient
    name: str,
    *,
    labels: dict[str, str],
    internal: bool = False,
    options: dict[str, str] | None = None,
) -> str:
    """Creates ``name`` on a free /28 of ``RIG_POOL``; returns its gateway address."""
    candidates = (str(s) for s in random.sample(list(RIG_POOL.subnets(new_prefix=RIG_PREFIX)), _ATTEMPTS))
    subnet = create_on_free_subnet(client, name, candidates, labels=labels, internal=internal, options=options)
    return str(next(ipaddress.ip_network(subnet).hosts()))


# The egress proxy's control plane binds this alias's address, declared on the rig network alone (as Compose
# declares it on sandbox_plane alone): a container name is registered on every network the container joins, the
# per-sandbox ones included, and Docker's resolver answers it from any one of them (E2E4).
EGRESS_CONTROL_ALIAS = "egress-proxy-control"


def remove_network_quietly(client: Any, name: str) -> None:  # noqa: ANN401 - docker.DockerClient
    """Teardown that never masks the test's own failure: a network already gone (or still busy) is left be."""
    try:
        client.api.remove_network(name)
    except Exception:
        return


def control_alias_config(client: Any, network: str) -> Any:  # noqa: ANN401 - docker-py's NetworkingConfig
    """``networking_config`` for a proxy container created on ``network`` with ``EGRESS_CONTROL_ALIAS``."""
    return client.api.create_networking_config(
        {network: client.api.create_endpoint_config(aliases=[EGRESS_CONTROL_ALIAS])}
    )
