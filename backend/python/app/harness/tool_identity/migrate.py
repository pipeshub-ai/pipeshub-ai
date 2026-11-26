"""``python -m app.harness.tool_identity.migrate [--apply | --revert]``: P2.2's agent toolset migration.

Without a flag it is a dry run: it prints what would change and every agent it would hold back
(ambiguous, no instance, a duplicate, an unknown org), and writes nothing. ``--apply`` re-keys;
``--revert`` undoes what ``--apply`` wrote. The report is JSON on stdout: ids and counts, no names
of people and no credentials. Run it where the query service runs (same ``DATA_STORE`` and
configuration store).
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

from app.harness.tool_identity.migration import AgentToolsetMigration, Mode

if TYPE_CHECKING:
    from collections.abc import Sequence

    from app.config.configuration_service import ConfigurationService
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

Stores = Callable[[logging.Logger], Awaitable["tuple[IGraphDBProvider, ConfigurationService]"]]


async def deployment_stores(logger: logging.Logger) -> tuple[IGraphDBProvider, ConfigurationService]:
    from app.config.configuration_service import ConfigurationService
    from app.config.providers.encrypted_store import EncryptedKeyValueStore
    from app.services.graph_db.graph_db_provider_factory import GraphDBProviderFactory

    config = ConfigurationService(logger=logger, key_value_store=EncryptedKeyValueStore(logger=logger))
    return await GraphDBProviderFactory.create_provider(logger, config), config


def parse_mode(argv: Sequence[str]) -> Mode:
    parser = argparse.ArgumentParser(prog="python -m app.harness.tool_identity.migrate", description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--apply", action="store_true", help="re-key type-keyed toolsets to their one instance")
    group.add_argument("--revert", action="store_true", help="undo what --apply wrote")
    args = parser.parse_args(list(argv))
    return "apply" if args.apply else "revert" if args.revert else "dry_run"


async def main(argv: Sequence[str], stores: Stores = deployment_stores) -> int:
    mode = parse_mode(argv)
    logger = logging.getLogger("app.harness.tool_identity.migrate")
    graph, config = await stores(logger)
    report = await AgentToolsetMigration(graph, config, now_ms=lambda: int(time.time() * 1000)).run(mode)
    sys.stdout.write(json.dumps(report.as_json(), indent=2, sort_keys=True) + "\n")
    return 1 if report.conflicts else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
