"""identity_0008: a slug a caller chooses is claimed across the install through identity.org_slug_claim, which answers a taken slug
exactly as a malformed one and writes nothing when it refuses."""
import time
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest

from .conftest import run_migrate
from .test_graph import as_role
from .test_integration import connect

A, B, C = "a" * 24, "b" * 24, "c" * 24
CLAIM = "SELECT identity.org_slug_claim(%s)"
ORG = ("INSERT INTO identity.orgs (org_id, slug, domain, contact_email, account_type, created_at, updated_at) "
       "VALUES (%s, %s, 'example.com', 'o@example.com', 'business', now(), now())")


def test_a_slug_is_claimed_once_and_a_taken_one_is_refused_as_a_malformed_one(rdb):
    cluster, name = rdb
    with connect(cluster, name) as c:
        c.execute(ORG, (C, "imported-slug"))                                          # an organisation written without a claim (an import)
        as_role(c, "pipeshub_node", A)
        assert c.execute(CLAIM, ("acme",)).fetchone()[0] is True
        as_role(c, "pipeshub_node", B)
        for refused in ("acme", "imported-slug", "Acme", "a--b", "-a", "org-1001", "org-7", "x" * 65):
            assert c.execute(CLAIM, (refused,)).fetchone()[0] is False, refused
        assert c.execute(CLAIM, ("acme-two",)).fetchone()[0] is True
        assert c.execute(CLAIM, ("acme-three",)).fetchone()[0] is False             # one claim per organisation
        c.execute("RESET ROLE")
        assert c.execute("SELECT org_id, slug FROM identity.org_slugs ORDER BY org_id").fetchall() == [(A, "acme"), (B, "acme-two")]


def test_no_service_role_reaches_the_slug_table(rdb):
    cluster, name = rdb
    with connect(cluster, name) as c:
        for role in ("pipeshub_node", "pipeshub_connectors", "pipeshub_indexing", "pipeshub_query", "pipeshub_jobs", "pipeshub_import"):
            assert not c.execute("SELECT has_table_privilege(%s, 'identity.org_slugs', 'SELECT, INSERT, UPDATE, DELETE')", (role,)).fetchone()[0], role


def test_a_claim_without_an_org_is_refused(rdb):
    cluster, name = rdb
    with connect(cluster, name) as c:
        as_role(c, "pipeshub_node")
        assert c.execute(CLAIM, ("unbound",)).fetchone()[0] is False


COPY = "SELECT identity.copied_org_slug_claim(%s, %s)"


def _import(c, org, slug):
    """The document copy's unit: the organisation row and its slug claim, as the import role (left open: the caller commits)."""
    c.execute("SET ROLE pipeshub_import")
    c.execute(ORG, (org, slug))
    return c.execute(COPY, (org, slug)).fetchone()[0]


def test_the_copy_claims_its_slug_once_and_a_taken_one_fails_its_unit(rdb):
    cluster, name = rdb
    with connect(cluster, name) as c:
        c.execute("SET ROLE pipeshub_import")
        assert c.execute(COPY, (A, "copied")).fetchone()[0] is True
        assert c.execute(COPY, (A, "copied")).fetchone()[0] is False              # a batch run again
        with pytest.raises(psycopg.errors.UniqueViolation):
            c.execute(COPY, (B, "copied"))
        for role in ("pipeshub_node", "pipeshub_connectors", "pipeshub_jobs"):
            assert not c.execute("SELECT has_function_privilege(%s, 'identity.copied_org_slug_claim(text, text)', 'EXECUTE')",
                                 (role,)).fetchone()[0], role
        c.execute("RESET ROLE")
        assert c.execute("SELECT org_id, slug FROM identity.org_slugs").fetchall() == [(A, "copied")]


@pytest.mark.parametrize("first", ["claim", "copy"])
def test_a_claim_racing_the_copy_for_one_slug_has_exactly_one_winner(rdb, first):
    """The two units overlap: the second blocks on the first's uncommitted key and loses once the first commits."""
    cluster, name = rdb
    claim, copy = (psycopg.connect(host="127.0.0.1", port=cluster.port, user="postgres", dbname=name) for _ in range(2))
    try:
        as_role(claim, "pipeshub_node", A)
        if first == "claim":
            assert claim.execute(CLAIM, ("raced",)).fetchone()[0] is True
            with ThreadPoolExecutor(1) as pool:
                lost = pool.submit(_import, copy, B, "raced")
                time.sleep(0.5)
                assert not lost.done()                                                # waiting on the claim's key
                claim.commit()
                with pytest.raises(psycopg.errors.UniqueViolation):
                    lost.result(timeout=30)
            copy.rollback()
            winner = A
        else:
            assert _import(copy, B, "raced") == 1
            with ThreadPoolExecutor(1) as pool:
                lost = pool.submit(lambda: claim.execute(CLAIM, ("raced",)).fetchone()[0])
                time.sleep(0.5)
                assert not lost.done()
                copy.commit()
                assert lost.result(timeout=30) is False
            claim.commit()
            winner = B
        with connect(cluster, name) as c:
            assert c.execute("SELECT org_id FROM identity.org_slugs WHERE slug = 'raced'").fetchall() == [(winner,)]
    finally:
        claim.close()
        copy.close()


def _org_slugs_after_upgrade(cluster, name, slug):
    """identity_0008 applied to an install whose one organisation holds ``slug``: what identity.org_slugs then holds."""
    url = cluster.url(name, "pipeshub_migrate")
    r = run_migrate(url, "downgrade", "identity_0007", PG_MIGRATE_ALLOW_DOWNGRADE="1")
    assert r.returncode == 0, r.stdout
    with connect(cluster, name) as c:
        c.execute("DELETE FROM identity.orgs")
        c.execute(ORG, (A, slug))
    r = run_migrate(url)
    assert r.returncode == 0, r.stdout
    with connect(cluster, name) as c:
        return c.execute("SELECT org_id, slug FROM identity.org_slugs").fetchall()


def test_the_upgrade_claims_the_slug_an_existing_organisation_chose(rdb):
    """The backfill reads identity.orgs as the vetted owner: as the owner it reads no row (forced row security) and claims nothing."""
    cluster, name = rdb
    assert _org_slugs_after_upgrade(cluster, name, "chosen-before") == [(A, "chosen-before")]


def test_the_upgrade_claims_no_counter_slug(rdb):
    cluster, name = rdb
    assert _org_slugs_after_upgrade(cluster, name, "org-42") == []
