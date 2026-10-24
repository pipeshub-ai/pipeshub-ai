"""A Gmail group whose members cannot be read is recorded as kept; a group of groups is not empty."""

from test_gmail_team_sync_behaviour import ALICE, BOB, Harness, ws  # noqa: F401


async def test_a_failed_member_read_notes_the_group_as_kept(ws: Harness, backoff_sleeps: list[float]) -> None:  # noqa: F811
    ws.directory.add_group("team@example.com", [ALICE, BOB])
    await ws.sync()
    ws.records.kept_user_groups.clear()
    ws.http.fail("GET", "/admin/directory/v1/groups/team@example.com/members", 500, "backendError")

    await ws.sync()

    assert ws.records.kept_user_groups == ["team@example.com"]


async def test_a_group_whose_members_all_lack_an_email_is_noted_as_kept(ws: Harness) -> None:  # noqa: F811
    ws.directory.add_group("team@example.com", [ALICE])
    await ws.sync()
    ws.records.kept_user_groups.clear()
    connector = await ws.connector_()

    async def members(_: str) -> list[dict]:
        return [{"id": "x1", "type": "USER"}]

    connector._fetch_group_members = members  # type: ignore[method-assign]
    await connector._process_group({"email": "team@example.com", "name": "team"})

    assert ws.records.kept_user_groups == ["team@example.com"]


async def test_a_group_of_groups_gets_the_nested_members_not_an_empty_list(ws: Harness) -> None:  # noqa: F811
    ws.directory.add_group("team@example.com", [ALICE, BOB])
    ws.directory.add_group("all@example.com", ["team@example.com"])

    await ws.sync()

    assert {m.email for m in ws.records.user_groups["all@example.com"][1]} == {ALICE, BOB}
