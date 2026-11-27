"""identity_0008: a slug a caller chooses is claimed across the install through identity.org_slug_claim, which answers a taken slug
exactly as a malformed one and writes nothing when it refuses."""
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
