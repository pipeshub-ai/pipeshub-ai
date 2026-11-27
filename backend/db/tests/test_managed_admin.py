"""A managed PostgreSQL: the administrator the platform hands out is not a superuser. It can create roles and databases (CREATEROLE,
CREATEDB), owns the database, and the platform's own superuser installs the allow-listed extensions. The role bootstrap and the
migration run with it, and the catalog and surface checks accept it as the bootstrap administrator, as they accept the superuser:
on PostgreSQL 16 and later it holds ADMIN (without INHERIT or SET) on every role it created. Nothing is relaxed for any other role."""
import psycopg
import pytest

from db import bootstrap_roles, surface

from .conftest import EXTENSIONS, VETTED, _drop_roles, run_migrate

pytestmark = pytest.mark.postgres
ADMIN = "platform_admin"


@pytest.fixture
def managed(cluster):
    """A database owned by a CREATEROLE, CREATEDB administrator that is not a superuser, bootstrapped and migrated by it."""
    name = "t_managed_admin"
    with cluster.admin() as su:
        su.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        _drop_roles(su)
        su.execute(f"DROP ROLE IF EXISTS {ADMIN}")
        su.execute(f"CREATE ROLE {ADMIN} LOGIN CREATEROLE CREATEDB NOSUPERUSER NOBYPASSRLS")
        su.execute(f"CREATE DATABASE {name} OWNER {ADMIN}")
    try:
        with psycopg.connect(host="127.0.0.1", port=cluster.port, user=ADMIN, dbname=name, autocommit=True) as c:
            bootstrap_roles.bootstrap(c, VETTED)
        with cluster.admin(name) as su:  # the platform's extension step: the allow-listed extensions, in the layout's schema
            su.execute("CREATE SCHEMA IF NOT EXISTS ext AUTHORIZATION pipeshub_owner")
            for e in EXTENSIONS:
                su.execute(f"CREATE EXTENSION IF NOT EXISTS {e} WITH SCHEMA ext")
        yield cluster, name
    finally:
        with cluster.admin() as su:
            su.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
            _drop_roles(su)
            su.execute("DROP ROLE IF EXISTS probe_actor")
            su.execute(f"DROP ROLE IF EXISTS {ADMIN}")


def _violations(cluster, name):
    with cluster.admin(name) as c:
        return [tuple(r) for r in c.execute("SELECT rule, object FROM kernel.rls_violations ORDER BY 1, 2")] + surface.violations(c)


def test_a_non_superuser_administrator_bootstraps_and_migrates_with_every_check_passing(managed):
    cluster, name = managed
    with cluster.admin(name) as c:
        assert c.execute(f"SELECT rolsuper FROM pg_roles WHERE rolname = '{ADMIN}'").fetchone()[0] is False
        if cluster.major >= 16:  # the implicit grants this test is about
            assert c.execute(f"SELECT count(*) FROM pg_auth_members WHERE member = '{ADMIN}'::regrole AND admin_option").fetchone()[0] > 0
    r = run_migrate(cluster.url(name, "pipeshub_migrate"))
    assert r.returncode == 0, r.stdout
    assert _violations(cluster, name) == []
    with cluster.admin(name) as c:  # the large-object functions belong to the platform's superuser: a warning, not a failure
        assert c.execute("SELECT count(*) FROM kernel.rls_warnings").fetchone()[0] > 0


@pytest.mark.parametrize(("plant", "rule"), [
    # any other role that can reach the administrator is an actor and holds what the administrator holds
    (f"CREATE ROLE probe_actor LOGIN; GRANT {ADMIN} TO probe_actor", "13 CREATE on database for app actor/PUBLIC"),
    # the administrator may hold our roles only with ADMIN: with INHERIT or SET it acts with an application role's privileges
    (f"GRANT pipeshub_query TO {ADMIN} WITH INHERIT TRUE", "37 bootstrap administrator holds an application role"),
    (f"GRANT pipeshub_owner TO {ADMIN} WITH SET TRUE", "37 bootstrap administrator holds an application role"),
])
def test_the_administrator_is_still_checked_where_it_meets_the_application(managed, plant, rule):
    cluster, name = managed
    assert run_migrate(cluster.url(name, "pipeshub_migrate")).returncode == 0
    with cluster.admin(name) as su:
        su.execute(plant)
    found = _violations(cluster, name)
    assert [v for v in found if v[0].startswith(rule)], found


def test_a_createrole_role_that_does_not_own_the_database_is_not_an_administrator(managed):
    cluster, name = managed
    assert run_migrate(cluster.url(name, "pipeshub_migrate")).returncode == 0
    with cluster.admin() as su:
        su.execute("CREATE ROLE pipeshub_extra_admin_probe LOGIN CREATEROLE")
    try:
        found = _violations(cluster, name)
        assert ("41 role holds CREATEROLE", "pipeshub_extra_admin_probe") in found, found
    finally:
        with cluster.admin() as su:
            su.execute("DROP ROLE pipeshub_extra_admin_probe")
