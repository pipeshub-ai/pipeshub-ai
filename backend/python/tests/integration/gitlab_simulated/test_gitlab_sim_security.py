"""Token sent where it should not go, SSRF through links in content, unbounded downloads,
unsafe header and filename handling."""

from tests.integration.gitlab_simulated.glsim.model import Membership


async def test_upload_link_cannot_reach_another_projects_files(world, org) -> None:
    """Anyone who can comment on the synced project writes an "attachment" link whose
    filename climbs out of /uploads/. The connector must not fetch another project's
    private file with its own token and index it where this project's members read it."""
    gl, p = world.gl, org["project"]
    vault = gl.add_project(org["group"], "vault", members={org["maya"]: 40},
                           files={"secrets.yml": "db_password: hunter2-PRIVATE\n"})
    payload = (f"[logs](/uploads/0123456789abcdef0123456789abcdef/../../../../projects/{vault.id}"
               f"/repository/files/secrets.yml/raw?ref=main)")
    issue = gl.add_issue(p, "Crash report", author=org["gus"], description="see attached")
    gl.add_note(issue, payload, author=org["gus"])
    await world.sync()
    leaked = [c for c in gl.calls if f"/projects/{vault.id}/" in c.path]
    assert not leaked, f"the connector requested {leaked[0].path} with its token"
    for doc in (await world.by_type("FILE")).values():
        if "/uploads/" in doc["externalRecordId"]:
            body = await world.stream(doc["externalRecordId"])
            assert b"hunter2" not in body


async def test_image_link_cannot_reach_another_projects_files(world, org) -> None:
    gl, p = world.gl, org["project"]
    vault = gl.add_project(org["group"], "vault", members={org["maya"]: 40},
                           files={"key.txt": "PRIVATE-KEY-MATERIAL\n"})
    payload = (f"![x](/uploads/0123456789abcdef0123456789abcdef/../../../../projects/{vault.id}"
               f"/repository/files/key.txt/raw?ref=main&x=.png)")
    gl.add_issue(p, "Screenshot", author=org["gus"], description=payload)
    await world.sync()
    leaked = [c for c in gl.calls if f"/projects/{vault.id}/" in c.path]
    assert not leaked, f"the connector requested {leaked[0].path} with its token"


async def test_link_to_another_host_is_never_fetched(world, org) -> None:
    gl, p = world.gl, org["project"]
    gl.add_issue(p, "External", author=org["gus"],
                 description="[x](https://evil.example/uploads/0123456789abcdef0123456789abcdef/a.pdf) "
                             "![y](http://169.254.169.254/uploads/0123456789abcdef0123456789abcdef/b.png)")
    await world.sync()
    assert not [d for d in (await world.by_type("FILE")) if "evil.example" in d or "169.254" in d]


async def test_oversized_image_is_not_read_into_memory(world, org) -> None:
    """Images are inlined up to 4 MB; a 40 MB upload must not be downloaded whole first."""
    gl, p = world.gl, org["project"]
    href = gl.add_upload(p, "huge.png", b"\x89PNG" + b"0" * (40 * 1024 * 1024))
    issue = gl.add_issue(p, "Big image", author=org["dev"], description=f"![huge]({href})")
    await world.sync()
    # The server counts what it got onto the socket; kernel buffers hold a few MB more
    # than the client read, so the line is drawn at half the image, not at the 4 MB cap.
    sent = gl.bytes_sent.get(href.split("/uploads/")[1], 0)
    assert sent < 20 * 1024 * 1024, f"{sent // (1024 * 1024)} MB of the image were read"
    assert issue


async def test_odd_titles_and_filenames_stream_safely(world, org) -> None:
    gl, p = world.gl, org["project"]
    href = gl.add_upload(p, "résumé “final”.pdf", b"%PDF data")
    issue = gl.add_issue(p, 'Title with "quotes" \\ and ☃ snowman', author=org["dev"],
                         description=f"[cv]({href})")
    gl.commit(p, {"docs/über “plan”.md": "# plan\n"})
    await world.sync()
    assert not world.index_failures, world.index_failures
    await world.stream(str(issue.id))


async def test_a_guest_never_reads_code_through_an_attachment_record(world, org) -> None:
    gl, p = world.gl, org["project"]
    p.members[org["gus"].id] = Membership(10)
    href = gl.add_upload(p, "design.pdf", b"%PDF")
    gl.add_mr(p, "MR with file", author=org["dev"], description=f"[d]({href})")
    await world.sync()
    diff = await world.diff()
    assert not diff.over, diff
