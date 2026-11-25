"""``python -m app.harness.credentials.migrate [--apply | --revert]``: CPD-8's credential migration.

Without a flag it is a dry run: per org, kind, installation and owner it prints what it would copy,
what is already copied and what it holds back (an owner with no org, an instance the owner's org
cannot see, a binding it did not import), and writes nothing. ``--apply`` copies and verifies;
``--revert`` removes what ``--apply`` imported, so reads fall back to the old paths. The report is
JSON on stdout: ids, outcomes and counts, never a credential. Run it where the connectors service
runs (same configuration store and ``DATA_STORE``), with Node reachable.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from app.harness.credentials.migration import (
    CredentialBindingMigration,
    GraphOwnerOrgs,
    Mode,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

Build = Callable[[logging.Logger], Awaitable[CredentialBindingMigration]]


async def deployment_migration(logger: logging.Logger) -> CredentialBindingMigration:
    from app.config.configuration_service import ConfigurationService
    from app.config.providers.encrypted_store import EncryptedKeyValueStore
    from app.harness.credentials.binding_writer import node_binding_writer
    from app.harness.credentials.bindings import BindingDocuments
    from app.harness.credentials.vault import VaultSecretStore
    from app.services.graph_db.graph_db_provider_factory import GraphDBProviderFactory
    from harness_core.clock import SystemClock

    config = ConfigurationService(logger=logger, key_value_store=EncryptedKeyValueStore(logger=logger))
    graph = await GraphDBProviderFactory.create_provider(logger, config)
    return CredentialBindingMigration(
        config,
        GraphOwnerOrgs(graph),
        await node_binding_writer(config),
        BindingDocuments(VaultSecretStore(config, SystemClock())),
        now_ms=lambda: int(time.time() * 1000),
    )


def parse_mode(argv: Sequence[str]) -> Mode:
    parser = argparse.ArgumentParser(prog="python -m app.harness.credentials.migrate", description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--apply", action="store_true", help="copy toolset and MCP credentials into bindings")
    group.add_argument("--revert", action="store_true", help="undo what --apply imported")
    args = parser.parse_args(list(argv))
    return "apply" if args.apply else "revert" if args.revert else "dry_run"


async def main(argv: Sequence[str], build: Build = deployment_migration) -> int:
    mode = parse_mode(argv)
    migration = await build(logging.getLogger("app.harness.credentials.migrate"))
    report = await migration.run(mode)
    sys.stdout.write(json.dumps(report.as_json(), indent=2, sort_keys=True) + "\n")
    return 1 if report.mismatched or (mode == "revert" and report.held) else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
