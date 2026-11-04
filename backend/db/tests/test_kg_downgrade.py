"""Taking the kg writers away (a development downgrade to kg_0001) leaves a database the catalog check accepts, and an upgrade brings them back."""
from .conftest import run_migrate
from .test_integration import connect


def test_downgrading_the_writers_leaves_a_clean_catalog_and_an_upgrade_brings_them_back(db):
    cluster, name = db
    url = cluster.url(name, "pipeshub_migrate")
    r = run_migrate(url)
    assert r.returncode == 0, r.stdout
    r = run_migrate(url, "downgrade", "kg_0001", PG_MIGRATE_ALLOW_DOWNGRADE="1")
    assert r.returncode == 0, r.stdout
    with connect(cluster, name) as c:
        assert [tuple(x) for x in c.execute("SELECT rule, object FROM kernel.rls_violations")] == []
        assert c.execute("SELECT count(*) FROM pg_proc WHERE pronamespace = 'kg'::regnamespace").fetchone()[0] == 0
    r = run_migrate(url)
    assert r.returncode == 0, r.stdout
    with connect(cluster, name) as c:
        assert c.execute("SELECT count(DISTINCT proc) FROM kernel.vetted_write_pins WHERE rel::text LIKE 'kg.%'").fetchone()[0] == 16
