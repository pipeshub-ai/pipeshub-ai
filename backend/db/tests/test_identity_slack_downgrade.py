"""A development downgrade of identity_0006 (accounts a Slack bot creates) with such accounts present.
The users table forces row-level security, which hides every org's rows from the role that runs the migration: a delete that did not lift it
removed nothing, and the narrower kind check then refused the rows that were left."""
from .conftest import run_migrate
from .test_integration import connect

ORG_A, ORG_B = "a" * 24, "b" * 24


def _org(c, org, domain):
    c.execute("INSERT INTO identity.orgs (org_id, domain, contact_email, account_type, registered_name, created_at, updated_at) "
              "VALUES (%s, %s, %s, 'business', 'Org', now(), now())", (org, domain, f"o@{domain}"))


def _user(c, org, uid, kind, slack=False):
    c.execute("INSERT INTO identity.users (org_id, id, slug, full_name, email, role, kind, slack_user_id, slack_team_id, created_at, updated_at) "
              "VALUES (%s, %s, %s, 'P', %s, 'member', %s, %s, %s, now(), now())",
              (org, uid, f"user-{uid}", f"{uid}@x.test", kind, "U1" if slack else None, "T1" if slack else None))


def test_the_downgrade_removes_slack_only_accounts_of_every_org_and_keeps_everyone_else(rdb):
    cluster, name = rdb
    url = cluster.url(name, "pipeshub_migrate")
    with connect(cluster, name) as c:
        _org(c, ORG_A, "a.test")
        _org(c, ORG_B, "b.test")
        _user(c, ORG_A, "1" * 24, "human")
        _user(c, ORG_A, "2" * 24, "slack", slack=True)
        _user(c, ORG_B, "3" * 24, "slack", slack=True)
        _user(c, ORG_B, "4" * 24, "service")
        c.execute("INSERT INTO identity.user_activities (org_id, id, user_id, email, activity_type, ip_address, details, created_at, updated_at) "
                  "VALUES (%s, %s, %s, 's@x.test', 'ACCOUNT CREATED FROM SLACK', 'system', 'created from Slack bot c', now(), now())", (ORG_A, "5" * 24, "2" * 24))

    r = run_migrate(url, "downgrade", "identity_0005", PG_MIGRATE_ALLOW_DOWNGRADE="1")
    assert r.returncode == 0, r.stdout + r.stderr

    with connect(cluster, name) as c:
        assert sorted(x for (x,) in c.execute("SELECT id FROM identity.users")) == ["1" * 24, "4" * 24]
        assert c.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema = 'identity' AND table_name = 'users' "
                         "AND column_name IN ('slack_user_id', 'slack_team_id')").fetchone()[0] == 0
        # row-level security is back as it was: forced, so the owner is bound by it again
        assert c.execute("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid = 'identity.users'::regclass").fetchone() == (True, True)
        assert [tuple(x) for x in c.execute("SELECT rule, object FROM kernel.rls_violations")] == []

    r = run_migrate(url)
    assert r.returncode == 0, r.stdout + r.stderr
    with connect(cluster, name) as c:
        assert c.execute("SELECT relforcerowsecurity FROM pg_class WHERE oid = 'identity.users'::regclass").fetchone()[0] is True
