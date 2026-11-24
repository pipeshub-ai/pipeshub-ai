"""Operator command: write the permission scope fields of a connector instance's existing vector points, and show progress.

    python -m app.scripts.scope_projection run    --org ORG --connector APP [--connector APP ...] [--restart] [--max-pages N] [--close-open-mark]
    python -m app.scripts.scope_projection status --org ORG --connector APP [--connector APP ...]
    python -m app.scripts.scope_projection rewrite --org ORG --connector APP [--connector APP ...]
    python -m app.scripts.scope_projection sweep  --org ORG [--connector APP ...] [--interval SECONDS]
    python -m app.scripts.scope_projection view   --org ORG [--connector APP ...]

`run` refuses to start unless the scope fields are on (`ACL_SCOPE_FIELDS=on` in every service, so points written meanwhile carry
them too). One run handles `--max-pages` pages and prints `running` when there are more: run it again. It resumes at its saved
position, so it can be stopped and repeated. Exit code 1 when a run failed or any point was left stale.

`rewrite` rewrites the pending roots of each named connector once (the sweep's rewrite step, by hand) and prints what it did. Exit code 1 when a
rewrite failed or the connector needs the full back-fill instead; a connector another rewriter holds, or one that changed meanwhile, is retried by running it again.

`status` also shows the pending roots and the sweep's last error.

`sweep` closes the writes whose lease ran out and rewrites the pending roots of every connector of the organisation (or the ones named), one
pass, or again every ``--interval`` seconds until stopped (a listing that fails is logged and tried again at the next round). A connector that
needs a full back-fill and was back-filled before gets a few pages of one per pass. Exit code 1 when a connector failed (it is retried after a
growing wait, and the others go on).

`view` shows, per connector, what keeps its stamps from being used and what will clear it: pending roots, writes that opened the connector and
never closed it (and whether the sweep will close them), a failing sweep, a connector that needs the back-fill, and, on pgvector, group rows
computed ahead of the database's clock. Read-only. Exit code 1 when anything needs attention.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

EXIT_FAILED = 1


def _pages(raw: str) -> int:
    n = int(raw)
    if n < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return n


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scope_projection", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "status", "rewrite"):
        p = sub.add_parser(name)
        p.add_argument("--org", required=True)
        p.add_argument("--connector", action="append", required=True, help="connector instance (app) id")
    run = sub.choices["run"]
    run.add_argument("--restart", action="store_true")
    run.add_argument("--close-open-mark", action="store_true",
                     help="close an open unsafe mark (an unclassified write that never wrote its closing mark); only when no write of the organisation's connectors is still running")
    run.add_argument("--max-pages", type=_pages, default=20)
    view = sub.add_parser("view")
    view.add_argument("--org", required=True)
    view.add_argument("--connector", action="append", help="only these connector instances (default: every connector with scope state)")
    sweep = sub.add_parser("sweep")
    sweep.add_argument("--org", required=True)
    sweep.add_argument("--connector", action="append", help="only these connector instances (default: every connector of the organisation)")
    sweep.add_argument("--interval", type=_pages, help="seconds between passes; without it one pass is made")
    return parser


async def _sweep(args: argparse.Namespace, graph_provider: Any, pipeline: Any, logger: Any) -> int:  # noqa: ANN401
    from app.services.vector_db.scope_sweep import ScopeSweep, sweep_forever

    sweep = ScopeSweep(graph_provider, pipeline.vector_db_service, pipeline.collection_locator, logger=logger)

    async def orgs() -> list[str]:
        return [args.org]

    def report(org: str, rows: list) -> None:
        for r in rows:
            if r.action != "idle":
                sys.stdout.write(f"{org}/{r.connector_id}  {r.action}  {r.detail}\n")

    failed = await sweep_forever(sweep, orgs, args.interval or 0, log=logger, connectors=args.connector, report=report,
                                 rounds=None if args.interval else 1)
    return EXIT_FAILED if failed else 0


async def _rewrite(args: argparse.Namespace, graph_provider: Any, pipeline: Any, logger: Any) -> int:  # noqa: ANN401
    from app.services.vector_db.scope_rewrite import ScopeRewriteJob

    job = ScopeRewriteJob(graph_provider, pipeline.vector_db_service, pipeline.collection_locator, logger=logger)
    code = 0
    for connector in args.connector:      # one connector's failure does not hold the others
        try:
            r = await job.run_connector(args.org, connector)
        except Exception as exc:
            sys.stdout.write(f"{args.org}/{connector}  error {type(exc).__name__}: {exc}\n")
            code = EXIT_FAILED
            continue
        sys.stdout.write(f"{args.org}/{connector}  {r.state}  roots={len(r.roots)} released={r.released} written={r.points_written} "
                         f"touched={r.points_touched}\n")
        if r.state == "full_pass":
            code = EXIT_FAILED
    return code


async def _view(args: argparse.Namespace, graph_provider: Any, pipeline: Any, config: Any) -> int:  # noqa: ANN401
    from app.services.permissions.generation import current_generation
    from app.services.permissions.scope_fields import (
        env_scope_generation,
        set_resolved_generation,
    )
    from app.services.vector_db.scope_view import org_view

    # Judged by the deployment's generation. Read only: `register_scope_generation` would write enabled=False when this process's flag is off.
    try:
        set_resolved_generation(max(await current_generation(config), env_scope_generation()))
    except Exception as exc:
        sys.stderr.write(json.dumps({"error": f"scope generation unavailable: {type(exc).__name__}: {exc}"}) + "\n")
        return EXIT_FAILED
    view = await org_view(graph_provider, args.org, pipeline.vector_db_service if pipeline else None, connectors=args.connector)
    if pipeline is None:
        view.notes.append("vector_store_not_configured: the group rows of the vector store were not checked")
    for line in view.lines() or ["no connector of the organisation has scope state"]:
        sys.stdout.write(line + "\n")
    return EXIT_FAILED if view.needs_attention else 0


async def _main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    from app.config.constants.arangodb import CollectionNames
    from app.containers.indexing import IndexingAppContainer
    from app.core.org_context import for_org
    from app.modules.indexing.vector_membership_backfill import (
        _resolve_indexing_pipeline,
    )
    from app.services.permissions.wiring import register_scope_generation
    from app.services.vector_db.scope_backfill import ScopeBackfill, status
    from app.services.vector_db.scope_view import status_line

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
                    with for_org(args.org, job="scope-status"):
                        app = await graph_provider.get_document(connector, CollectionNames.APPS.value, raise_on_error=True) or {}
                    sys.stdout.write(f"{args.org}/{connector}  " + (row.line() if row else "not started") + f"; {status_line(app)}\n")
                except Exception as exc:  # one connector's failure does not hide the others
                    sys.stdout.write(f"{args.org}/{connector}  error {type(exc).__name__}: {exc}\n")
                    code = EXIT_FAILED
            return code
        pipeline = await _resolve_indexing_pipeline(container)
        if args.command == "view":
            return await _view(args, graph_provider, pipeline, container.config_service())
        if pipeline is None:
            sys.stderr.write(json.dumps({"error": "no indexing pipeline (vector store not configured)"}) + "\n")
            return EXIT_FAILED
        from app.services.permissions.mode import scope_fields_enabled

        if not scope_fields_enabled():
            # before registering: a process with the flag off records the deployment as switched off, and the next start with it on
            # moves the generation, so every stamp already written goes stale
            sys.stderr.write(json.dumps({"error": f"turn the scope fields on (ACL_SCOPE_FIELDS) before the {args.command}"}) + "\n")
            return EXIT_FAILED
        # The stamps this run writes carry the deployment's generation, which a service resolves at start; so does this process.
        await register_scope_generation(container.config_service(), logger)
        if args.command == "sweep":
            return await _sweep(args, graph_provider, pipeline, logger)
        if args.command == "rewrite":
            return await _rewrite(args, graph_provider, pipeline, logger)
        job = ScopeBackfill(graph_provider, pipeline.vector_db_service, pipeline.collection_locator, logger)
        code = 0
        for connector in args.connector:
            try:
                progress = await job.run_connector(args.org, connector, restart=args.restart, max_pages=args.max_pages,
                                                  close_open_mark=args.close_open_mark)
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
