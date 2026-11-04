"""Operator command: write the permission scope fields of a connector instance's existing vector points, and show progress.

    python -m app.scripts.scope_projection run    --org ORG --connector APP [--connector APP ...] [--restart] [--max-pages N]
    python -m app.scripts.scope_projection status --org ORG --connector APP [--connector APP ...]

`run` refuses to start unless the scope fields are on (`ACL_SCOPE_FIELDS=on` in every service, so points written meanwhile carry
them too). One run handles `--max-pages` pages and prints `running` when there are more: run it again. It resumes at its saved
position, so it can be stopped and repeated. Exit code 1 when a run failed or any point was left stale.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

EXIT_FAILED = 1


def _pages(raw: str) -> int:
    n = int(raw)
    if n < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return n


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scope_projection", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "status"):
        p = sub.add_parser(name)
        p.add_argument("--org", required=True)
        p.add_argument("--connector", action="append", required=True, help="connector instance (app) id")
    run = sub.choices["run"]
    run.add_argument("--restart", action="store_true")
    run.add_argument("--max-pages", type=_pages, default=20)
    return parser


async def _main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    from app.containers.indexing import IndexingAppContainer
    from app.modules.indexing.vector_membership_backfill import (
        _resolve_indexing_pipeline,
    )
    from app.services.permissions.wiring import register_scope_generation
    from app.services.vector_db.scope_backfill import ScopeBackfill, status

    container = IndexingAppContainer.init("scope_projection")
    logger = container.logger()
    try:
        graph_provider = await container.graph_provider()
    except Exception as exc:
        sys.stderr.write(json.dumps({"error": f"graph unavailable: {type(exc).__name__}: {exc}"}) + "\n")
        return EXIT_FAILED
    try:
        if args.command == "status":
            code = 0
            for connector in args.connector:
                try:
                    row = await status(graph_provider, args.org, connector)
                    sys.stdout.write(f"{args.org}/{connector}  " + (row.line() if row else "not started") + "\n")
                except Exception as exc:  # one connector's failure does not hide the others
                    sys.stdout.write(f"{args.org}/{connector}  error {type(exc).__name__}: {exc}\n")
                    code = EXIT_FAILED
            return code
        pipeline = await _resolve_indexing_pipeline(container)
        if pipeline is None:
            sys.stderr.write(json.dumps({"error": "no indexing pipeline (vector store not configured)"}) + "\n")
            return EXIT_FAILED
        # The stamps this run writes carry the deployment's generation, which a service resolves at start; so does this process.
        await register_scope_generation(container.config_service(), logger)
        job = ScopeBackfill(graph_provider, pipeline.vector_db_service, pipeline.collection_locator, logger)
        code = 0
        for connector in args.connector:
            try:
                progress = await job.run_connector(args.org, connector, restart=args.restart, max_pages=args.max_pages)
            except Exception as exc:
                sys.stdout.write(f"{args.org}/{connector}  error {type(exc).__name__}: {exc}\n")
                code = EXIT_FAILED
                continue
            sys.stdout.write(f"{args.org}/{connector}  {progress.line()}\n")
            if progress.state == "failed" or progress.stale:
                code = EXIT_FAILED
        return code
    except Exception as exc:
        logger.exception("scope_projection failed")
        sys.stderr.write(json.dumps({"error": f"{type(exc).__name__}: {exc}"}) + "\n")
        return EXIT_FAILED
    finally:
        await graph_provider.disconnect()


if __name__ == "__main__":
    sys.exit(asyncio.run(_main(sys.argv[1:])))
