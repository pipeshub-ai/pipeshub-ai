"""Content streaming, reindexing, indexing filters, filter options and the connection test."""

import pytest
from fastapi import HTTPException

from tests.integration.gitlab_simulated.helpers import ends, fail, pid


async def test_issue_content_has_body_comments_and_images(world, org) -> None:
    gl, p = world.gl, org["project"]
    img = gl.add_upload(p, "shot.png", b"\x89PNG\r\n\x1a\nfakepng")
    issue = gl.add_issue(p, "Bug", author=org["dev"], description=f"steps to reproduce\n\n![shot]({img})")
    gl.add_note(issue, "I can reproduce", author=org["rita"])
    await world.sync()
    text = await world.stream_text(str(issue.id))
    assert "steps to reproduce" in text and "I can reproduce" in text
    assert "data:image/png;base64," in text


async def test_merge_request_content_has_description_comments_commits_and_diffs(world, org) -> None:
    gl, p = world.gl, org["project"]
    from tests.integration.gitlab_simulated.glsim.server import diff_entry
    mr = gl.add_mr(p, "Add feature", author=org["dev"], description="adds the feature",
                   files=[diff_entry("src/app.py", "src/app.py", b"print('hi')\n", b"print('feature')\n")])
    gl.add_note(mr, "looks good", author=org["maya"])
    gl.add_note(mr, "nit: rename", author=org["maya"], position={"new_path": "src/app.py", "old_path": "src/app.py"})
    await world.sync()
    text = await world.stream_text(str(mr.id))
    for part in ("adds the feature", "looks good", "nit: rename", "feature')", "commit body"):
        assert part in text, part


async def test_code_file_streams_the_synced_content(world, org) -> None:
    p = org["project"]
    await world.sync()
    body = await world.stream(f"/{p.path_with_namespace}/-/blob/HEAD/src/app.py")
    assert body == b"print('hi')\n"


async def test_attachment_streams_its_bytes(world, org) -> None:
    gl, p = world.gl, org["project"]
    href = gl.add_upload(p, "spec.pdf", b"%PDF-1.4 the spec")
    gl.add_issue(p, "With file", author=org["dev"], description=f"[spec]({href})")
    await world.sync()
    (url,) = [k for k in await world.by_type("FILE") if "/uploads/" in k]
    assert await world.stream(url) == b"%PDF-1.4 the spec"


async def test_reindex_refreshes_a_changed_issue_and_requeues_the_rest(world, org) -> None:
    gl, p = world.gl, org["project"]
    a = gl.add_issue(p, "A", author=org["dev"])
    b = gl.add_issue(p, "B", author=org["dev"])
    await world.sync()
    a.title = "A changed"
    gl.touch(a)
    records = [await world.typed(str(a.id)), await world.typed(str(b.id))]
    world.producer.events.clear()
    await world.connector.reindex_records(records)
    assert "A changed" in await world.names("TICKET")
    assert world.producer.of_type("reindexRecord") or world.producer.of_type("updateRecord")


async def test_indexing_filters_turn_types_off(world, org) -> None:
    gl, p = world.gl, org["project"]
    gl.add_issue(p, "Issue", author=org["dev"])
    gl.add_mr(p, "MR", author=org["dev"])
    world.set_indexing(issues=False, merge_requests=False, code_files=False)
    await world.sync()
    docs = (await world.live_docs()).values()
    assert {"TICKET", "PULL_REQUEST", "CODE_FILE"} <= {d.get("recordType") for d in docs}
    for d in docs:
        assert d.get("indexingStatus") == "AUTO_INDEX_OFF", d.get("recordName")


async def test_filter_options_list_projects_and_groups(world, org) -> None:
    gl = world.gl
    gl.add_project(org["group"], "gadgets")
    await world.start()
    res = await world.connector.get_filter_options("project_ids", search="gad")
    assert res.success and [o.id for o in res.options] == ["acme/gadgets"], res
    res = await world.connector.get_filter_options("group_ids")
    assert res.success and "acme" in [o.id for o in res.options], res


async def test_connection_test(world, org) -> None:
    await world.start()
    assert await world.connector.test_connection_and_access() is True


async def test_stream_of_a_deleted_issue_says_gone(world, org) -> None:
    gl, p = world.gl, org["project"]
    issue = gl.add_issue(p, "Gone soon", author=org["dev"])
    await world.sync()
    issue.deleted = True
    try:
        await world.stream(str(issue.id))
    except Exception as e:
        assert "404" in repr(e) or "not found" in repr(e).lower() or "Gone" in repr(e) or getattr(e, "status_code", 0) in (404, 410)
    else:
        raise AssertionError("streamed a deleted issue")


async def test_comment_listing_failing_while_streaming_is_not_indexed_as_empty(world, org) -> None:
    gl, p = world.gl, org["project"]
    issue = gl.add_issue(p, "Has comments", author=org["dev"])
    gl.add_note(issue, "important detail", author=org["rita"])
    await world.sync(index=False)
    record = await world.typed(str(issue.id))
    fail(world, ends(f"{pid(p)}/issues/{issue.iid}/notes"), status=502, times=20)
    with pytest.raises(HTTPException) as err:
        response = await world.connector.stream_record(record)
        _ = [chunk async for chunk in response.body_iterator]
    assert err.value.status_code >= 500, "an issue whose comments could not be read streamed as if it had none"
