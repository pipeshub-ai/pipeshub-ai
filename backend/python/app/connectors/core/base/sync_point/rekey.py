"""``python -m app.connectors.core.base.sync_point.rekey [--apply]``: the sync-point step of the ``SECRET_KEY``
rotation (ADR-KEY-01 D8, WP87-FU-4b).

A connector seals some fields of its sync points (delta links) with ``sync_point_cipher`` and marks each one
``<field>_encrypted``. Without ``--apply`` this counts every org's sync points that hold a sealed field by the key
that sealed them; with it, a sync point with a field under the previous key has those fields sealed again under the
current one by a compare-and-set on their stored values, so one a connector rewrote meanwhile is left as the
connector wrote it (and counted ``previous``: the final check reads it again). Everything goes through
``IGraphDBProvider`` (``list_sync_points``, ``update_sync_point_if``), on any graph backend. Nothing is
checkpointed: a second run finds what is left. Prints one JSON line of counts, never a value or a key.
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING, Any, TextIO

from app.config.constants.arangodb import CollectionNames
from app.core.org_context import for_org, system
from app.utils.encryption.encryption_service import DecryptionError
from app.utils.encryption.rotation_step import RekeyReport, report_step, run_cli
from app.utils.encryption.rotation_step import parse_apply as _parse_apply

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
    from app.utils.encryption.encryption_service import EncryptionService

STEP = "sync-points"
JOB = "secret-key-rotation"
PAGE = 500
_MARKER = "_encrypted"


async def rekey(
    graph: IGraphDBProvider,
    cipher: EncryptionService,
    *,
    apply: bool,
    page: int = PAGE,
    orgs: Callable[[IGraphDBProvider], Awaitable[list[str]]] | None = None,
) -> RekeyReport:
    """``orgs`` is every org in the graph unless a caller names its own (a test sharing a database)."""
    report = RekeyReport(STEP)
    collection = CollectionNames.SYNC_POINTS.value
    for org_id in await (orgs or all_org_ids)(graph):
        with for_org(org_id, job=JOB):
            after: str | None = None
            while True:
                points = await graph.list_sync_points(org_id, collection, after_key=after, limit=page)
                for point in points:
                    await _rekey_one(graph, cipher, point, collection, report, apply=apply)
                if len(points) < page:
                    break
                after = str(points[-1]["syncPointKey"])
    return report


async def all_org_ids(graph: IGraphDBProvider) -> list[str]:
    """Every org, active or not, external or not: a deactivated org's sync points are still under a key."""
    with system(JOB):
        orgs = [
            org
            for external in (False, True)
            for org in await graph.get_all_orgs(active=False, is_external=external, raise_on_error=True)
        ]
    return sorted({str(key) for org in orgs if (key := org.get("_key") or org.get("id"))})


def sealed_fields(point: dict[str, Any]) -> dict[str, Any]:
    """The fields a sync point holds sealed, by the marker ``SyncPoint`` writes beside each."""
    return {
        field: point[field]
        for marker, flag in point.items()
        if marker.endswith(_MARKER) and flag is True and (field := marker.removesuffix(_MARKER)) in point
    }


async def _rekey_one(
    graph: IGraphDBProvider,
    cipher: EncryptionService,
    point: dict[str, Any],
    collection: str,
    report: RekeyReport,
    *,
    apply: bool,
) -> None:
    sealed = sealed_fields(point)
    if not sealed:
        return
    report.total += 1
    stale: dict[str, str] = {}
    for field, value in sealed.items():
        try:
            plaintext, current = cipher.decrypt_detailed(value)
        except DecryptionError:
            report.unreadable += 1
            return
        if not current:
            stale[field] = plaintext
    if not stale:
        report.current += 1
        return
    if apply and await graph.update_sync_point_if(
        str(point["syncPointKey"]),
        {field: sealed[field] for field in stale},
        {field: cipher.encrypt(plaintext) for field, plaintext in stale.items()},
        collection,
    ):
        report.moved += 1
        report.current += 1
    else:
        report.previous += 1


async def deployment_state(logger: logging.Logger) -> tuple[IGraphDBProvider, EncryptionService]:
    from app.config.configuration_service import ConfigurationService
    from app.config.providers.encrypted_store import EncryptedKeyValueStore
    from app.connectors.core.base.sync_point.sync_point import sync_point_cipher
    from app.services.graph_db.graph_db_provider_factory import GraphDBProviderFactory

    cipher = sync_point_cipher()
    config = ConfigurationService(logger=logger, key_value_store=EncryptedKeyValueStore(logger=logger))
    return await GraphDBProviderFactory.create_provider(logger, config), cipher


def parse_apply(argv: Sequence[str]) -> bool:
    return _parse_apply("python -m app.connectors.core.base.sync_point.rekey", __doc__, argv)


async def main(
    argv: Sequence[str],
    build: Callable[[logging.Logger], Awaitable[tuple[IGraphDBProvider, EncryptionService]]] = deployment_state,
    out: TextIO | None = None,
) -> int:
    apply = parse_apply(argv)

    async def work() -> RekeyReport:
        graph, cipher = await build(logging.getLogger("app.connectors.core.base.sync_point.rekey"))
        return await rekey(graph, cipher, apply=apply)

    return await report_step(STEP, work, sys.stdout if out is None else out, _own_error)


def _own_error(exc: Exception) -> bool:
    from app.utils.encryption.secret_key_ring import SecretKeyRingError

    return isinstance(exc, SecretKeyRingError)


def cli(argv: Sequence[str]) -> int:
    """The process entry (``run_cli``: stdout carries the report line alone)."""
    return run_cli(lambda report: main(argv, deployment_state, report))


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]))


__all__ = ["STEP", "all_org_ids", "cli", "deployment_state", "main", "parse_apply", "rekey", "sealed_fields"]
