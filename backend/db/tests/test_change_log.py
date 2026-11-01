"""The change log (kernel_0013, platform_data_0003): triggers on the document-store tables write platform.docstore_changes as the role that
changed the row; the copy and a restore are not logged; the log is invisible to the application and to other organisations."""
import psycopg
import pytest

from .test_integration import connect

A, B = "a" * 24, "b" * 24
DOC = ("INSERT INTO content.documents (org_id, id, document_name, storage_vendor, created_at, updated_at) "
       "VALUES (%s, %s, 'x', 'local', now(), now())")


def as_node(c, org):
    c.execute("SET ROLE pipeshub_node")
    c.execute("SELECT set_config('app.org_id', %s, false)", (org,))


def log(c):
    c.execute("RESET ROLE")
    return [tuple(r) for r in c.execute("SELECT org_id, rel, pk, op FROM platform.docstore_changes ORDER BY id")]


def test_a_change_is_logged_with_its_document_and_operation(rdb):
    cluster, name = rdb
    with connect(cluster, name) as c:
        as_node(c, A)
        c.execute(DOC, (A, "1" * 24))
        c.execute("UPDATE content.documents SET document_name = 'y'")
        c.execute("DELETE FROM content.documents")
        assert log(c) == [(A, "content.documents", "1" * 24, op) for op in "IUD"]


def test_a_child_row_names_its_parent_document(rdb):
    cluster, name = rdb
    with connect(cluster, name) as c:
        as_node(c, A)
        c.execute("INSERT INTO content.projects (org_id, id, user_id, name, last_activity_at, created_at, updated_at) VALUES (%s, %s, %s, 'p', now(), now(), now())", (A, "2" * 24, "3" * 24))
        c.execute("INSERT INTO content.project_members (org_id, project_id, principal_type, principal_id, role, added_by, added_at, pos) VALUES (%s, %s, 'user', %s, 'viewer', %s, now(), 0)", (A, "2" * 24, "4" * 24, "3" * 24))
        c.execute("UPDATE content.project_members SET role = 'editor'")
        c.execute("DELETE FROM content.projects")        # the member row goes with it (a cascade runs as the owner and is not logged)
        # (whether the cascaded delete of the member row is logged too depends on the major version; either way the project names it)
        got = [e for e in log(c) if e != (A, "content.project_members", "2" * 24, "D")]
        assert got == [(A, "content.projects", "2" * 24, "I"), (A, "content.project_members", "2" * 24, "I"),
                          (A, "content.project_members", "2" * 24, "U"), (A, "content.projects", "2" * 24, "D")]


def test_the_copy_and_a_restore_are_not_logged(rdb):
    cluster, name = rdb
    with connect(cluster, name) as c:
        c.execute("SET ROLE pipeshub_import")
        c.execute(DOC, (A, "5" * 24))
        c.execute("UPDATE content.documents SET document_name = 'z'")
        c.execute("DELETE FROM content.documents")
        assert log(c) == []


@pytest.mark.parametrize("stmt", ["SELECT 1 FROM platform.docstore_changes", "DELETE FROM platform.docstore_changes",
                                  "UPDATE platform.docstore_changes SET pk = 'x'"])
def test_the_application_cannot_read_or_change_the_log(rdb, stmt):
    cluster, name = rdb
    with connect(cluster, name) as c:
        as_node(c, A)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute(stmt)


def test_the_follower_reads_every_organisations_changes(rdb):
    cluster, name = rdb
    with connect(cluster, name) as c:
        for org, doc in ((A, "6" * 24), (B, "7" * 24)):
            as_node(c, org)
            c.execute(DOC, (org, doc))
            c.execute("RESET ROLE")
        as_node(c, B)
        assert c.execute("SELECT count(*) FROM content.documents").fetchone()[0] == 1       # another organisation's rows stay invisible
        c.execute("RESET ROLE")
        c.execute("SET ROLE pipeshub_import")
        assert c.execute("SELECT count(*) FROM platform.docstore_changes").fetchone()[0] == 2
        assert c.execute("DELETE FROM platform.docstore_changes WHERE id <= (SELECT min(id) FROM platform.docstore_changes)").rowcount == 1


def test_every_role_that_can_write_a_triggered_table_can_write_the_log(rdb):
    cluster, name = rdb
    with connect(cluster, name) as c:
        tables = [r[0] for r in c.execute("SELECT DISTINCT tgrelid::regclass::text FROM pg_trigger WHERE tgname = 'docstore_change_log' AND NOT tgisinternal "
                                          "AND NOT EXISTS (SELECT 1 FROM pg_inherits i WHERE i.inhrelid = tgrelid)")]
        assert len(tables) == 30
        skipped = {"pipeshub_import", "pipeshub_restore", "pipeshub_owner", "pipeshub_migrate"}
        for t in tables:
            for (role,) in c.execute("SELECT rolname FROM pg_roles WHERE rolname LIKE 'pipeshub\\_%%' AND rolname <> ALL(%s) AND ("
                                     "has_table_privilege(oid, %s, 'INSERT') OR has_table_privilege(oid, %s, 'UPDATE') OR has_table_privilege(oid, %s, 'DELETE'))",
                                     (sorted(skipped), t, t, t)).fetchall():
                assert c.execute("SELECT has_table_privilege(%s, 'platform.docstore_changes', 'INSERT')", (role,)).fetchone()[0], f"{role} writes {t}"
