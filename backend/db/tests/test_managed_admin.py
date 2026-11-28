"""A managed PostgreSQL: the administrator the platform hands out is not a superuser. It can create roles and databases (CREATEROLE,
CREATEDB), owns the database, and the platform's own superuser installs the allow-listed extensions. The role bootstrap and the
migration run with it, and the catalog and surface checks accept it as the bootstrap administrator, as they accept the superuser:
on PostgreSQL 16 and later it holds ADMIN (without INHERIT or SET) on every role it created. Nothing is relaxed for any other role,
and an owner that also holds an attribute reaching around row security is not the administrator."""
import contextlib

import psycopg
import pytest
from psycopg import sql

from db import bootstrap_roles, surface

from .conftest import EXTENSIONS, VETTED, _drop_roles, run_migrate

pytestmark = pytest.mark.postgres
ADMIN = "platform_admin"
DB = "t_managed_admin"
ADMIN_ATTRS = "LOGIN CREATEROLE CREATEDB NOSUPERUSER NOBYPASSRLS"
PROBES = ("probe_actor", "probe_provider_admin", "platform_admin extra")


def _ident(name: str) -> sql.Composable:
    return sql.Identifier(name)


@contextlib.contextmanager
def _managed(cluster, admin: str = ADMIN, attrs: str = ADMIN_ATTRS):
    """A database owned by ``admin`` (``attrs``), bootstrapped by it; the platform's superuser installs the extensions."""
    def clean(su) -> None:
        su.execute(f"DROP DATABASE IF EXISTS {DB} WITH (FORCE)")
        _drop_roles(su)
        for r in (*PROBES, admin):
            su.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(_ident(r)))

    with cluster.admin() as su:
        clean(su)
        su.execute(sql.SQL("CREATE ROLE {} " + attrs).format(_ident(admin)))
        su.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(_ident(DB), _ident(admin)))
    try:
        with psycopg.connect(host="127.0.0.1", port=cluster.port, user=admin, dbname=DB, autocommit=True) as c:
            bootstrap_roles.bootstrap(c, VETTED)
        with cluster.admin(DB) as su:  # the platform's extension step: the allow-listed extensions, in the layout's schema
            su.execute("CREATE SCHEMA IF NOT EXISTS ext AUTHORIZATION pipeshub_owner")
            for e in EXTENSIONS:
                su.execute(f"CREATE EXTENSION IF NOT EXISTS {e} WITH SCHEMA ext")
        yield cluster, DB
    finally:
        with cluster.admin() as su:
            clean(su)


@pytest.fixture
def managed(cluster):
    with _managed(cluster) as m:
        yield m


def _migrate(cluster, name):
    return run_migrate(cluster.url(name, "pipeshub_migrate"))


def _violations(cluster, name):
    with cluster.admin(name) as c:
        return [tuple(r) for r in c.execute("SELECT rule, object FROM kernel.rls_violations ORDER BY 1, 2")] + surface.violations(c)


def test_a_non_superuser_administrator_bootstraps_and_migrates_with_every_check_passing(managed):
    cluster, name = managed
    with cluster.admin(name) as c:
        assert c.execute(f"SELECT rolsuper FROM pg_roles WHERE rolname = '{ADMIN}'").fetchone()[0] is False
        if cluster.major >= 16:  # the implicit grants this test is about
            assert c.execute(f"SELECT count(*) FROM pg_auth_members WHERE member = '{ADMIN}'::regrole AND admin_option").fetchone()[0] > 0
    r = _migrate(cluster, name)
    assert r.returncode == 0, r.stdout
    assert _violations(cluster, name) == []
    with cluster.admin(name) as c:  # the large-object functions belong to the platform's superuser: a warning, not a failure
        assert c.execute("SELECT count(*) FROM kernel.rls_warnings").fetchone()[0] > 0


# Names that a pattern, a quoting rule or a token substitution could get wrong: '_' is a LIKE wildcard ('pipeshubx_admin' matched
# 'pipeshub_%'), a name that needs quoting renders differently in the role line and in regrole text, and 'bootstrap' occurs in the
# superuser's token itself.
# ... and 'owner' is also a field label in surface lines ('database owner=...').
@pytest.mark.parametrize("admin", ["pipeshubx_admin", "Platform Admin", "bootstrap", "owner"])
def test_any_administrator_name_passes(cluster, admin):
    with _managed(cluster, admin) as (c, name):
        r = _migrate(c, name)
        assert r.returncode == 0, r.stdout
        assert _violations(c, name) == []


def test_the_administrator_may_hold_a_role_of_the_platforms_own(managed):
    """A managed service's administrator is usually a member of the service's own administrative role (inherited); that role is
    not ours and holds nothing of ours, so it is the platform's business, as a superuser's memberships are."""
    cluster, name = managed
    with cluster.admin() as su:
        su.execute(f"CREATE ROLE probe_provider_admin NOLOGIN; GRANT probe_provider_admin TO {ADMIN}")
    r = _migrate(cluster, name)
    assert r.returncode == 0, r.stdout
    assert _violations(cluster, name) == []


@pytest.mark.parametrize(("plant", "rule"), [
    # any other role that can reach the administrator is an actor and holds what the administrator holds
    (f"CREATE ROLE probe_actor LOGIN; GRANT {ADMIN} TO probe_actor", "13 CREATE on database for app actor/PUBLIC"),
    # the administrator may hold our roles only with ADMIN: with INHERIT or SET it acts with an application role's privileges
    (f"GRANT pipeshub_query TO {ADMIN} WITH INHERIT TRUE", "37 bootstrap administrator holds an application role"),
    (f"GRANT pipeshub_owner TO {ADMIN} WITH SET TRUE", "37 bootstrap administrator holds an application role"),
    # only PUBLIC's access to the large-object functions is the platform's: a grant to one of ours is still check 43
    ("GRANT EXECUTE ON FUNCTION pg_catalog.lo_create(oid) TO pipeshub_query", "43 role can use large object function lo_create(oid)"),
])
def test_the_administrator_is_still_checked_where_it_meets_the_application(managed, plant, rule):
    cluster, name = managed
    assert _migrate(cluster, name).returncode == 0
    with cluster.admin(name) as su:
        su.execute(plant)
    found = _violations(cluster, name)
    assert [v for v in found if v[0].startswith(rule)], found


@pytest.mark.parametrize(("attrs", "violation"), [
    # REPLICATION reaches around row security (BYPASSRLS is check 22 already): such an owner stays an actor, and check 41 reports it
    (ADMIN_ATTRS + " REPLICATION", ("41 role holds REPLICATION", ADMIN)),
    # the administrator has both CREATEROLE and CREATEDB: an owner with CREATEROLE alone is not one
    ("LOGIN CREATEROLE NOCREATEDB NOSUPERUSER NOBYPASSRLS", ("41 role holds CREATEROLE", ADMIN)),
])
def test_an_owner_that_is_not_the_documented_administrator_stays_checked(cluster, attrs, violation):
    with _managed(cluster, ADMIN, attrs) as (c, name):
        r = _migrate(c, name)  # the failed check rolls the upgrade back: what it found is in its output
        assert r.returncode != 0 and f"VIOLATION {violation!r}" in r.stdout, r.stdout


def test_a_createrole_role_that_does_not_own_the_database_is_not_an_administrator(managed):
    cluster, name = managed
    assert _migrate(cluster, name).returncode == 0
    with cluster.admin() as su:
        su.execute("CREATE ROLE pipeshub_extra_admin_probe LOGIN CREATEROLE")
    try:
        assert ("41 role holds CREATEROLE", "pipeshub_extra_admin_probe") in _violations(cluster, name)
    finally:
        with cluster.admin() as su:
            su.execute("DROP ROLE pipeshub_extra_admin_probe")


def test_a_role_the_administrator_only_created_is_still_in_the_surface(managed):
    """Holding ADMIN on a role is what creating it gives; that role is not the platform's, and a new one is reviewed."""
    cluster, name = managed
    assert _migrate(cluster, name).returncode == 0
    with psycopg.connect(host="127.0.0.1", port=cluster.port, user=ADMIN, dbname=name, autocommit=True) as c:
        c.execute("CREATE ROLE probe_actor LOGIN")
    found = _violations(cluster, name)
    assert [v for v in found if v[0] == "50 surface: unexpected" and v[1].startswith("role probe_actor ")], found


def test_an_owner_that_cannot_log_in_is_not_the_administrator(managed):
    cluster, name = managed
    assert _migrate(cluster, name).returncode == 0
    with cluster.admin() as su:
        su.execute(f"ALTER ROLE {ADMIN} NOLOGIN")
    assert ("41 role holds CREATEROLE", ADMIN) in _violations(cluster, name)


def test_a_role_whose_name_extends_the_administrators_is_still_in_the_surface(managed):
    cluster, name = managed
    assert _migrate(cluster, name).returncode == 0
    with psycopg.connect(host="127.0.0.1", port=cluster.port, user=ADMIN, dbname=name, autocommit=True) as c:
        c.execute('CREATE ROLE "platform_admin extra" LOGIN')
    found = _violations(cluster, name)
    assert [v for v in found if v[0] == "50 surface: unexpected" and v[1].startswith("role platform_admin extra login=")], found


@pytest.mark.parametrize("attrs", ["LOGIN", "NOLOGIN CREATEDB"])
def test_a_held_role_that_can_log_in_or_holds_an_attribute_is_still_in_the_surface(managed, attrs):
    """Only a NOLOGIN group with none of the attributes checks 22 and 41 report is the platform's: nothing else reports a login the
    administrator holds."""
    cluster, name = managed
    assert _migrate(cluster, name).returncode == 0
    with cluster.admin() as su:
        su.execute(f"CREATE ROLE probe_actor {attrs}; GRANT probe_actor TO {ADMIN} WITH INHERIT TRUE")
    found = {v[1] for v in _violations(cluster, name) if v[0] == "50 surface: unexpected"}
    assert any(x.startswith("role probe_actor login=") for x in found), found
    assert any(x.startswith(f"member {ADMIN} -> probe_actor ") for x in found), found
