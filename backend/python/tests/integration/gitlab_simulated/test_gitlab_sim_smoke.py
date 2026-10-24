async def test_first_sync_matches_gitlab(world, org) -> None:
    gl, p = world.gl, org["project"]
    issue = gl.add_issue(p, "Login fails", author=org["dev"], description="steps")
    gl.add_note(issue, "seen it too", author=org["rita"])
    gl.add_mr(p, "Fix login", author=org["dev"], files=[])
    await world.sync()
    diff = await world.diff()
    assert diff.clean(), diff
    assert "Login fails" in await world.names("TICKET")
    assert "Fix login" in await world.names("PULL_REQUEST")
    assert not world.index_failures, world.index_failures
