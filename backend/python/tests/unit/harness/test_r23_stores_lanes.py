"""R23-STORES: the e2e lane's two store and network options, read from the files the lane runs (deployment/e2e).

WP114-FU-E2E (``E2E_DOC_STORE=postgres``, ``run.sh pg``): Node's document store on PostgreSQL behind PgBouncer in
transaction mode, with the jobs login, migrated before the app starts. C15.04 (``E2E_AIRGAP=1``, ``run.sh airgap``):
no network of the stack routes out, and only the ingress publishes a port. The running lanes prove the rest
(pg_world.py, airgap_check.py); these keep the wiring from drifting from what Node reads and what the schema holds.
"""

from __future__ import annotations

import asyncio
import os
import re
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parents[5]
E2E = REPO / "deployment" / "e2e"
RUN_SH = E2E / "run.sh"
DB_VERSIONS = REPO / "backend" / "db" / "versions"
DOC_STORE_TS = REPO / "backend/nodejs/apps/src/libs/repositories/doc-store.ts"


class _ComposeLoader(yaml.SafeLoader):
    """Compose's merge tags (``!reset``, ``!override``) read as the value they carry."""


_ComposeLoader.add_constructor("!reset", lambda loader, node: ("reset", loader.construct_sequence(node)
                                                                if isinstance(node, yaml.SequenceNode) else None))
_ComposeLoader.add_constructor("!override", lambda loader, node: ("override", loader.construct_sequence(node)
                                                                   if isinstance(node, yaml.SequenceNode)
                                                                   else loader.construct_mapping(node)))


def _compose(path: Path) -> dict[str, Any]:
    return yaml.load(path.read_text(), Loader=_ComposeLoader)  # SafeLoader with two constructors


def _env(service: dict[str, Any]) -> dict[str, str]:
    return dict(item.split("=", 1) for item in service.get("environment", []))


def _function(name: str) -> str:
    script = RUN_SH.read_text()
    start = script.index(f"{name}() {{")
    return script[start:script.index("\n}\n", start) + 3]


def test_r23_pg_1_the_app_gets_the_settings_node_reads_through_pgbouncer_with_listen_direct() -> None:
    env = _env(_compose(E2E / "docker-compose.pg.yml")["services"]["pipeshub-ai"])
    assert env["DOC_STORE"] == "postgres"
    assert env["PG_POOLER"] == "pgbouncer_txn"
    assert re.fullmatch(r"postgresql://pipeshub_node_login:\$\{PG_NODE_PASSWORD\}@pgbouncer:6432/\$\{PG_DATABASE\}",
                        env["PG_NODE_URL"])
    assert re.fullmatch(r"postgresql://pipeshub_jobs:\$\{PG_JOBS_PASSWORD\}@pgbouncer:6432/\$\{PG_DATABASE\}",
                        env["PG_JOBS_URL"])
    # LISTEN needs a session: it bypasses the transaction pooler.
    assert "@postgres:5432/" in env["PG_LISTEN_URL"]
    node_reads = set(re.findall(r"process\.env\.(PG_[A-Z_]+)", DOC_STORE_TS.read_text()))
    assert {"PG_NODE_URL", "PG_JOBS_URL", "PG_POOLER"} <= node_reads


def test_r23_pg_2_pgbouncer_runs_transaction_mode_with_the_targeted_reset_and_prepared_statements(
        tmp_path: Path) -> None:
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "pgbouncer").write_text('#!/bin/sh\ncat "$1"\n')
    (fake / "pgbouncer").chmod(0o755)
    script = (E2E / "pgbouncer.sh").read_text().replace("dir=/tmp/pgbouncer", f"dir={tmp_path}/pgb")
    done = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30, check=True,
                          env={**os.environ, "PATH": f"{fake}:{os.environ['PATH']}", "PG_DATABASE": "pipeshub",
                               "PG_NODE_PASSWORD": "n-secret", "PG_JOBS_PASSWORD": "j-secret"})
    ini = done.stdout
    assert "pool_mode = transaction" in ini
    assert "server_reset_query_always = 1" in ini
    reset = next(line for line in ini.splitlines() if line.startswith("server_reset_query ="))
    for step in ("RESET ALL", "SET SESSION AUTHORIZATION DEFAULT", "DISCARD TEMP", "UNLISTEN *",
                 "pg_advisory_unlock_all()", "DEALLOCATE ALL"):
        assert step in reset
    assert int(re.search(r"max_prepared_statements = (\d+)", ini)[1]) > 0
    users = (tmp_path / "pgb" / "users.txt").read_text()
    assert users == '"pipeshub_node_login" "n-secret"\n"pipeshub_jobs" "j-secret"\n'
    assert (tmp_path / "pgb" / "users.txt").stat().st_mode & 0o077 == 0


def test_r23_pg_3_the_schema_is_migrated_before_the_app_starts_and_each_run_has_its_own_passwords() -> None:
    up = _function("up_stack")
    assert up.index("migrate_pg") < up.index("compose start pipeshub-ai")
    migrate = _function("migrate_pg")
    assert migrate.index("pg-migrate") < migrate.index("pgbouncer")
    prepare = _function("prepare")
    for name in ("PG_ADMIN_PASSWORD", "PG_MIGRATE_PASSWORD", "PG_NODE_PASSWORD", "PG_JOBS_PASSWORD"):
        assert f"{name}=$(rand 16)" in prepare
    migrate_sh = (E2E / "pg_migrate.sh").read_text()
    assert migrate_sh.index("db.bootstrap_roles") < migrate_sh.index("db.migrate\n")
    assert "@postgres:5432/" in migrate_sh and "pgbouncer" not in migrate_sh.split("set -euo pipefail", 1)[1]


def _created_tables() -> dict[str, str]:
    tables: dict[str, str] = {}
    for sql in DB_VERSIONS.glob("*/*.sql"):
        text = sql.read_text()
        for match in re.finditer(r"CREATE TABLE ([a-z_]+\.[a-z_]+) \((.*?)\n\);", text, re.S):
            tables[match[1]] = match[2]
    return tables


def test_r23_pg_4_every_table_and_column_pg_world_reads_is_one_the_migrations_create() -> None:
    sys.path.insert(0, str(E2E))
    try:
        import pg_world
    finally:
        sys.path.remove(str(E2E))
    tables = _created_tables()
    for name in (*pg_world.SESSION_DELETED, *pg_world.ORG_DELETE_TABLES):
        assert f"harness.{name}" in tables, name
        assert re.search(r"^\s*org_id text", tables[f"harness.{name}"], re.M), name
    assert re.search(r"^\s*session_id text", tables["harness.runs"], re.M)
    assert re.search(r"^\s*session_id text", tables["harness.session_events"], re.M)
    assert re.search(r"^\s*is_deleted boolean", tables["identity.orgs"], re.M)
    assert re.search(r"^\s*id conversation\.oid24", tables["conversation.chat_sessions"], re.M)


def test_r23_airgap_1_no_network_of_the_stack_routes_out_but_the_ingress_one() -> None:
    base = _compose(REPO / "deployment/docker-compose/docker-compose.yml")
    airgap = _compose(E2E / "docker-compose.airgap.yml")
    nets = {name: {**(base["networks"].get(name) or {}), **(airgap["networks"].get(name) or {})}
            for name in {*base["networks"], *airgap["networks"]}}
    routed = {name for name, net in nets.items() if not net.get("internal")}
    assert routed == {"ingress"}
    users = [name for name, svc in {**base["services"], **airgap["services"]}.items()
             if "ingress" in (airgap["services"].get(name, {}).get("networks") or [])]
    assert users == ["pxh-ingress"]


def test_r23_airgap_2_every_port_the_lane_publishes_moves_to_the_ingress() -> None:
    lane = _compose(E2E / "docker-compose.e2e.yml")
    airgap = _compose(E2E / "docker-compose.airgap.yml")["services"]
    published = {name for name, svc in lane["services"].items() if svc.get("ports")}
    assert published == {"pipeshub-ai", "pxh-fake-llm", "pxh-fake-saas"}
    for name in published:
        assert airgap[name]["ports"] == ("reset", []), name
    forwards = {arg.split("=", 1)[1] for arg in airgap["pxh-ingress"]["entrypoint"][2:]}
    assert forwards == {"pipeshub-ai:3000", "pxh-fake-llm:8080", "pxh-fake-saas:8090"}
    assert len(airgap["pxh-ingress"]["ports"]) == len(forwards)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_r23_airgap_3_the_ingress_forwards_both_ways_and_drops_a_connection_it_cannot_place(tmp_path: Path) -> None:
    async def scenario() -> tuple[bytes, bytes]:
        async def echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            writer.write(b"upstream:" + await reader.readexactly(5))
            await writer.drain()
            writer.close()

        upstream = await asyncio.start_server(echo, "127.0.0.1", 0)
        up_port = upstream.sockets[0].getsockname()[1]
        good, dead = _free_port(), _free_port()
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-I", str(E2E / "ingress.py"), f"{good}=127.0.0.1:{up_port}", f"{dead}=127.0.0.1:1",
            stdout=asyncio.subprocess.PIPE)
        try:
            assert proc.stdout is not None
            assert b"ready (2 ports)" in await asyncio.wait_for(proc.stdout.readline(), 20)
            reader, writer = await asyncio.open_connection("127.0.0.1", good)
            writer.write(b"hello")
            await writer.drain()
            forwarded = await asyncio.wait_for(reader.read(), 10)
            writer.close()
            reader, writer = await asyncio.open_connection("127.0.0.1", dead)
            dropped = await asyncio.wait_for(reader.read(), 10)
            writer.close()
            return forwarded, dropped
        finally:
            proc.terminate()
            await proc.wait()
            upstream.close()

    forwarded, dropped = asyncio.run(scenario())
    assert forwarded == b"upstream:hello"
    assert dropped == b""


@pytest.mark.parametrize("option", ["E2E_DOC_STORE", "E2E_AIRGAP"])
def test_r23_lanes_1_each_option_is_kept_in_the_stack_env_and_picks_its_overlay(option: str) -> None:
    prepare, compose = _function("prepare"), _function("compose")
    assert f"{option}=${{{option}:-" in prepare
    assert f'"$(envv {option})"' in compose


def test_r23_lanes_2_a_stack_left_up_runs_on_its_env_file_whatever_the_caller_exported(tmp_path: Path) -> None:
    # `durable-specs RUN` without E2E_SITE_PACKAGES exported it empty, which Compose preferred to the stack's file.
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "docker").write_text('#!/bin/sh\necho "site=${E2E_SITE_PACKAGES-unset} keep=${KEEP_ME-unset}"\n')
    (fake / "docker").chmod(0o755)
    env_file = tmp_path / "stack.env"
    env_file.write_text("# a comment\nE2E_SITE_PACKAGES=/the/stack/site\nCOMPOSE_PROFILES=\n")
    script = _function("compose")
    done = subprocess.run(
        ["bash", "-c", f"envv() {{ grep -E \"^$1=\" \"$ENV_FILE\" | head -1 | cut -d= -f2-; }}\n{script}\ncompose ps"],
        capture_output=True, text=True, timeout=30, check=True,
        env={**os.environ, "PATH": f"{fake}:{os.environ['PATH']}", "ENV_FILE": str(env_file), "PROJECT": "p",
             "BASE_COMPOSE": "b.yml", "OVERLAY": "o.yml", "E2E_SITE_PACKAGES": "", "KEEP_ME": "kept"})
    assert done.stdout.strip() == "site=unset keep=kept"
