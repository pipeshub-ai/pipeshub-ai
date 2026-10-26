"""db.wait_schema: an application pod waits until the migration has reached the head of its build."""
import pytest

from db import wait_schema

from .conftest import run_migrate


def test_behind_names_the_branches_below_the_head():
    assert wait_schema.behind({"a": 2, "b": 1}, {"a": 2, "b": 3, "c": 1}) == ["b 1 < 3", "c 0 < 1"]
    assert wait_schema.behind({"a": 3}, {"a": 2}) == []


def test_the_url_must_be_a_service_login(monkeypatch):
    monkeypatch.setenv("PG_WAIT_URL", "postgresql://pipeshub_jobs:x@127.0.0.1:1/pipeshub")
    with pytest.raises(ValueError):
        wait_schema.read_epochs("postgresql://pipeshub_jobs:x@127.0.0.1:1/pipeshub")


def test_a_malformed_url_or_timeout_is_a_configuration_error_not_a_wait(monkeypatch):
    monkeypatch.setenv("PG_WAIT_URL", "postgresql://[bad")
    assert wait_schema.main() == 2                                   # exit 2 at once, not an hour of retries ending in 1
    monkeypatch.setenv("PG_WAIT_URL", "postgresql://pipeshub_node_login@127.0.0.1:1/x")
    monkeypatch.setenv("PG_WAIT_TIMEOUT_S", "soon")
    assert wait_schema.main() == 2


def test_waits_for_the_migration_and_passes_once_at_head(db, monkeypatch, capsys):
    cluster, name = db
    url = cluster.url(name, "pipeshub_node_login").replace("postgresql+psycopg://", "postgresql://")
    monkeypatch.setenv("PG_WAIT_URL", url)
    monkeypatch.setenv("PG_WAIT_TIMEOUT_S", "1")
    assert wait_schema.main() == 1                                   # nothing migrated yet: kernel.schema_epoch does not exist
    assert "waiting for the migration" in capsys.readouterr().out
    assert run_migrate(cluster.url(name, "pipeshub_migrate")).returncode == 0
    assert wait_schema.main() == 0                                   # at head, read as the node login through its service role
    with cluster.admin(name) as c:
        c.execute("UPDATE kernel.schema_epoch SET epoch = 0 WHERE branch = 'platform'")
    assert wait_schema.main() == 1
    assert "platform 0 <" in capsys.readouterr().out


def test_permanent_errors_fail_fast_with_the_reason_and_the_rest_keeps_waiting(db, monkeypatch, capsys):
    cluster, name = db
    good = cluster.url(name, "pipeshub_node_login").replace("postgresql+psycopg://", "postgresql://")
    monkeypatch.setenv("PG_WAIT_TIMEOUT_S", "2")
    monkeypatch.setenv("PG_WAIT_URL", good.rsplit("/", 1)[0] + "/no_such_database")
    assert wait_schema.main() == 2                                    # an unknown database: nothing to wait for
    assert "waiting will not help" in capsys.readouterr().err
    monkeypatch.setenv("PG_WAIT_URL", good.replace("127.0.0.1", "127.0.0.1").replace(f":{cluster.port}/", ":1/"))
    assert wait_schema.main() == 1                                    # nothing listens yet: keep waiting until the deadline
    out = capsys.readouterr()
    assert "not reachable yet" in out.out and "timed out" in out.err


class _Stub(Exception):
    def __init__(self, sqlstate, text) -> None:
        super().__init__(text)
        self.sqlstate = sqlstate


@pytest.mark.parametrize("state, text, expected", [
    ("28P01", 'FATAL: password authentication failed for user "x"', True),
    ("28000", "FATAL: no pg_hba.conf entry", True),
    ("3D000", 'FATAL: database "x" does not exist', True),
    ("42501", "permission denied for table schema_epoch", True),
    ("42501", "permission denied for schema kernel", False),
    (None, "FATAL: SASL authentication failed", True),
    (None, "connection failed: Connection refused", False),
    ("57P03", "the database system is starting up", False),
    ("08006", "connection to server was lost", False),
])
def test_which_errors_are_permanent(state, text, expected):
    assert wait_schema.permanent(_Stub(state, text)) is expected


def test_a_missing_kernel_schema_is_reported_as_not_migrated_yet_not_as_an_epoch(db, monkeypatch, capsys):
    cluster, name = db
    monkeypatch.setenv("PG_WAIT_URL", cluster.url(name, "pipeshub_node_login").replace("postgresql+psycopg://", "postgresql://"))
    monkeypatch.setenv("PG_WAIT_TIMEOUT_S", "1")
    assert wait_schema.main() == 1
    assert "kernel schema does not exist yet" in capsys.readouterr().out
