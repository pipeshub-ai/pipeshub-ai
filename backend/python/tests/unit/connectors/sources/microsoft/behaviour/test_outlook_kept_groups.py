"""A failed Outlook group member read records the group as kept and syncs the rest of it."""

from ms_graph_fakes import graph_error, page
from test_outlook_sync_behaviour import (  # noqa: F401
    ANA,
    GROUP,
    api,
    connector,
    db,
    post,
    serve_group,
    stored_by_type,
    thread,
)

from app.models.entities import RecordType


async def test_a_failed_member_read_notes_the_group_as_kept(connector, api, db) -> None:  # noqa: F811
    serve_group(api, graph_error(500, "ErrorInternalServerError"))
    db.user_groups[GROUP] = [ANA]

    await connector.run_sync()

    assert db.kept_user_groups == [GROUP]


async def test_a_failed_member_read_still_syncs_the_group_mailbox_and_its_posts(connector, api, db) -> None:  # noqa: F811
    serve_group(api, graph_error(500, "ErrorInternalServerError"), threads=page([thread("t1", "Launch")]))
    api.on("GET", f"/v1.0/groups/{GROUP}/threads/t1/posts", page([post("p1")]))
    db.user_groups[GROUP] = [ANA]

    await connector.run_sync()

    assert db.user_groups[GROUP] == [ANA]
    assert GROUP in db.record_groups
    assert [r.external_record_id for r in stored_by_type(db, RecordType.GROUP_MAIL).values()] == ["p1"]
