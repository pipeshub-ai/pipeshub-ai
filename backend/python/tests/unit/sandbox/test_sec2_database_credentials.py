"""SEC2 O-4 (DB-1..DB-3), OWD-1 (WP-06): a database URL's password never enters a sandbox, in any form.

A broker run reaches the database through the egress proxy under ``DATABASE_HOST``: the sandbox's ``DATABASE_URL``
names the proxy and no password, and the password travels sealed in the run's egress route, for the proxy to sign
in with (``harness_core.sandbox.egress.postgres``). The Docker executor has no proxy, so a password is refused there
with the reason. Local mode (no sandbox: psql runs in this service) keeps its password file, removed before user SQL.
"""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent_loop_lib.sandbox.coding import credentials as cred
from app.sandbox.broker_executor import BrokerExecutor
from app.sandbox.docker_executor import DockerExecutor
from app.sandbox.local_executor import LocalExecutor
from app.sandbox.models import ExecutionResult, SandboxLanguage
from harness_core.sandbox.egress.policy import policy_errors
from harness_core.sandbox.egress.sealed import CredentialSealer
from harness_core.sandbox.types import CredentialInjection
from harness_core.testing.clock import ManualClock
from harness_core.testing.sandbox import FakeSandboxBroker
from harness_core.testing.sandbox.egress import FakeEgressProxy

if TYPE_CHECKING:
    from pathlib import Path

    from harness_core.sandbox.types import SandboxSpec
    from harness_core.testing.sandbox.fake import FakeProcessContext, FakeSandboxHandle

PASSWORD = "pg:pa\\ss word'"
URL = "postgresql://sandbox_ro:pg%3Apa%5Css%20word%27@db.corp.example:5433/analytics?sslmode=require"
PUBLIC = "postgresql://sandbox_ro@db.corp.example:5433/analytics?sslmode=require"
PGPASS = b"*:*:*:*:pg\\:pa\\\\ss word'\n"
DEPLOYMENT_KEY = "ec761ba3b3abdb780c1bebd0f56c79d81613ed8f9fb8c0e4eb7ac21ed714e5a7"


class TestDb1Pure:
    def test_url_form(self) -> None:
        found = cred.database(URL)
        assert found.url == PUBLIC and found.password == PASSWORD
        assert (found.host, found.port, found.dbname, found.user, found.sslmode) == (
            "db.corp.example", 5433, "analytics", "sandbox_ro", "require"
        )
        assert PASSWORD not in repr(found)

    def test_password_in_the_query(self) -> None:
        found = cred.database("postgres://u@h/db?password=p%3Aq&application_name=x")
        assert found.url == "postgres://u@h/db?application_name=x" and found.password == "p:q"

    def test_keyword_value_form(self) -> None:
        found = cred.database("host=h port=5432 password='a b\\'c' dbname=d user=u")
        assert found.url == "host=h port=5432 dbname=d user=u" and found.password == "a b'c"
        assert (found.host, found.port, found.dbname, found.user) == ("h", 5432, "d", "u")

    @pytest.mark.parametrize("url", ["postgresql://localhost/test", "postgresql://u@h:5432/d", "host=h dbname=d"])
    def test_without_a_password_the_url_is_kept(self, url: str) -> None:
        found = cred.database(url)
        assert found.url == url and found.password is None

    def test_the_local_password_file_escapes_libpqs_separators(self) -> None:
        assert cred.pgpass(PASSWORD) == PGPASS


def _no_password(*things: object) -> None:
    text = json.dumps([repr(t) for t in things])
    for needle in ("pg%3Apa", "pa\\\\ss", "pa\\ss", "ss word"):
        assert needle not in text


class TestDb2Docker:
    async def test_a_password_is_refused_where_no_proxy_can_add_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setattr("app.sandbox.docker_executor._sandbox_root", lambda: str(tmp_path))
        client = MagicMock()
        with patch("docker.from_env", return_value=client), caplog.at_level(logging.DEBUG):
            result = await DockerExecutor().execute("SELECT 1;", SandboxLanguage.POSTGRESQL.value, env={"DATABASE_URL": URL})
        assert not result.success and "SANDBOX_MODE=broker" in (result.error or "")
        client.containers.create.assert_not_called()
        _no_password(result, caplog.text)


class _Recording(FakeSandboxBroker):
    def __init__(self) -> None:
        clock = ManualClock()
        super().__init__(clock=clock, egress_proxy=FakeEgressProxy(clock=clock, gateways={}))
        self.specs: list[SandboxSpec] = []
        self.runs: list[dict[str, Any]] = []

    async def acquire(self, spec: SandboxSpec, **kwargs: Any) -> FakeSandboxHandle:  # noqa: ANN401
        self.specs.append(spec)
        return await super().acquire(spec, **kwargs)


class TestDb2Broker:
    async def test_the_sandbox_reaches_the_database_through_the_proxy_and_holds_no_password(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SECRET_KEY", DEPLOYMENT_KEY)
        broker = _Recording()

        async def sh(context: FakeProcessContext) -> int:
            files = context.sandbox.files
            broker.runs.append({"argv": list(context.argv), "env": dict(context.env), "files": {p: f.data for p, f in files.items()}})
            await context.stdout.put(b"a\n1\n")
            return 0

        broker.programs["sh"] = sh

        async def source() -> FakeSandboxBroker:
            return broker

        with caplog.at_level(logging.DEBUG):
            result = await BrokerExecutor(broker=source, image="img", work_root=str(tmp_path)).execute(
                "SELECT 1;", SandboxLanguage.POSTGRESQL.value, env={"DATABASE_URL": URL}
            )
        assert result.success, result.error
        [run] = broker.runs
        [spec] = broker.specs
        assert run["env"]["DATABASE_URL"] == "postgresql://sandbox_ro@pxh-database:5432/analytics?sslmode=disable"
        assert run["argv"] == ["sh", "-c", 'psql "$DATABASE_URL" -f /src/query.sql --csv']
        assert run["files"]["/src/query.sql"] == b"SELECT 1;"
        [destination] = spec.egress.destinations
        [route] = spec.egress.token_routes
        assert (destination.host, destination.ports) == (cred.DATABASE_HOST, (5432,))
        assert destination.upstream == "postgres://sandbox_ro@db.corp.example:5433/?sslmode=require"
        assert route.injection is CredentialInjection.POSTGRES and policy_errors(spec.egress) == []
        assert json.loads(CredentialSealer(DEPLOYMENT_KEY.encode()).open(route)) == {"user": "sandbox_ro", "password": PASSWORD}
        _no_password(run["argv"], run["env"], run["files"], spec, caplog.text)

    async def test_without_a_password_the_run_still_reaches_the_database_through_the_proxy(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SECRET_KEY", DEPLOYMENT_KEY)
        broker = _Recording()

        async def sh(context: FakeProcessContext) -> int:
            broker.runs.append({"env": dict(context.env)})
            return 0

        broker.programs["sh"] = sh

        async def source() -> FakeSandboxBroker:
            return broker

        await BrokerExecutor(broker=source, image="img", work_root=str(tmp_path)).execute(
            "SELECT 1;", SandboxLanguage.POSTGRESQL.value, env={"DATABASE_URL": "postgresql://h/d"}
        )
        [run] = broker.runs
        assert run["env"]["DATABASE_URL"] == "postgresql://pxh-database:5432/d?sslmode=disable"
        assert broker.specs[0].egress.destinations[0].upstream == "postgres://h:5432/?sslmode=prefer"
        assert [m.target for m in broker.specs[0].mounts] == ["/src", "/output"]

    async def test_without_the_deployment_key_nothing_runs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SECRET_KEY", raising=False)
        broker = _Recording()

        async def source() -> FakeSandboxBroker:
            return broker

        result = await BrokerExecutor(broker=source, image="img", work_root=str(tmp_path)).execute(
            "SELECT 1;", SandboxLanguage.POSTGRESQL.value, env={"DATABASE_URL": URL}
        )
        assert not result.success and "SECRET_KEY" in (result.error or "") and broker.specs == []


class TestDb3Local:
    async def test_argv_and_logs_hold_no_password_and_the_file_is_gone_after(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setattr("app.sandbox.local_executor._sandbox_root", lambda: str(tmp_path))
        seen: dict[str, Any] = {}

        async def subprocess(cmd: list[str], cwd: str, timeout: int, env: dict[str, str] | None, **_: object) -> ExecutionResult:
            path = (env or {})["PGPASSFILE"]
            seen.update(cmd=cmd, env=env, content=open(path, "rb").read(), mode=os.stat(path).st_mode & 0o777)
            return ExecutionResult(success=True, stdout="a\n1\n", exit_code=0)

        executor = LocalExecutor()
        with patch.object(executor, "_subprocess", new=AsyncMock(side_effect=subprocess)), caplog.at_level(logging.DEBUG):
            result = await executor.execute("SELECT 1;", SandboxLanguage.POSTGRESQL, env={"DATABASE_URL": URL})
        assert result.success
        assert seen["cmd"] == ["psql", PUBLIC, "-c", cred.PSQL_FORGET.strip(), "-c", "SELECT 1;", "--csv"]
        assert (seen["content"], seen["mode"]) == (PGPASS, 0o600)
        assert not os.path.exists(seen["env"]["PGPASSFILE"])
        _no_password(seen["cmd"], {k: v for k, v in seen["env"].items() if k != "PATH"}, caplog.text)

    async def test_without_a_password_the_argv_is_todays(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("app.sandbox.local_executor._sandbox_root", lambda: str(tmp_path))
        sub = AsyncMock(return_value=ExecutionResult(success=True, exit_code=0))
        executor = LocalExecutor()
        with patch.object(executor, "_subprocess", new=sub):
            await executor.execute("SELECT 1;", SandboxLanguage.POSTGRESQL, env={"DATABASE_URL": "postgresql://h/d"})
        assert sub.call_args.args[0] == ["psql", "postgresql://h/d", "-c", "SELECT 1;", "--csv"]
