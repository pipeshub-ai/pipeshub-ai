"""Token handling, SSRF through URLs in content, unbounded downloads, headers, logs.

Every test runs on Neo4j and ArangoDB (NEO4J_IT_URI / ARANGO_IT_URL) against the simulated
GitHub in ``sim/``; see README.md.
"""

import logging

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]


async def test_images_and_attachments_are_fetched_and_the_token_stays_on_the_api(world, org) -> None:
    """github.com/user-attachments refuses API tokens (404, live-verified) and redirects
    an anonymous request to signed storage. The token must not be sent to either host,
    and the content must arrive."""
    gh, repo = world.gh, org["repo"]
    repo.visibility = "public"
    image = gh.upload(repo, "shot.png", b"\x89PNG\r\n\x1a\n" + b"0" * 100, image=True)
    doc = gh.upload(repo, "notes.txt", b"meeting notes")
    gh.add_issue(repo, "Screenshot", body=f"Look: ![shot]({image}) and [notes.txt]({doc})")
    await world.sync()
    text = await world.stream_text(f"{repo.id}/issues/1")
    assert "data:image/png;base64" in text
    key = next(k for k in (await world.by_type("FILE")) if k.startswith("https://"))
    assert await world.stream(key) == b"meeting notes"
    assert not [c for c in gh.calls if c.host != "api.github.com" and c.authorization]
    assert not world.index_failures, world.index_failures



async def test_a_private_repository_s_attachments_are_fetched_with_the_token(world, org) -> None:
    """github.com/user-attachments answers an anonymous request for a private
    repository with 404. Only then is the token sent, to github.com and nowhere else."""
    gh, repo = world.gh, org["repo"]
    gh.behaviour.attachment_token_accepted = True
    image = gh.upload(repo, "shot.png", b"\x89PNG\r\n\x1a\n" + b"0" * 100, image=True)
    doc = gh.upload(repo, "notes.txt", b"meeting notes")
    gh.add_issue(repo, "Screenshot", body=f"Look: ![shot]({image}) and [notes.txt]({doc})")
    await world.sync()
    text = await world.stream_text(f"{repo.id}/issues/1")
    assert "data:image/png;base64" in text
    key = next(k for k in (await world.by_type("FILE")) if k.startswith("https://"))
    assert await world.stream(key) == b"meeting notes"
    attachment_calls = [c for c in gh.calls if c.host == "github.com"]
    assert [c.authorization for c in attachment_calls] == [False, True] * (len(attachment_calls) // 2)
    assert not [c for c in gh.calls if c.host not in ("api.github.com", "github.com") and c.authorization]
    assert not world.index_failures, world.index_failures

async def test_links_to_other_hosts_are_never_fetched(world, org) -> None:
    gh, repo = world.gh, org["repo"]
    body = ("![a](https://evil.example/user-attachments/assets/x) "
            "![b](https://github.com.evil.example/user-attachments/assets/x) "
            "[c](http://169.254.169.254/latest/meta-data) "
            "![d](http://github.com/user-attachments/assets/plain-http)")
    gh.add_issue(repo, "Links", body=body)
    await world.sync()
    await world.stream(f"{repo.id}/issues/1")
    assert not [c for c in gh.calls if c.host not in ("api.github.com", "github.com",
                                                      "private-user-images.githubusercontent.com")]
    assert not [c for c in gh.calls if c.host == "github.com" and "plain-http" in c.path]


@pytest.mark.parametrize("token_accepted", [False, True], ids=["public-anonymous", "private-token-accepted"])
async def test_huge_embedded_image_is_not_downloaded_whole(world, org, token_accepted) -> None:
    """An issue may embed any upload with image syntax, including a 100 MB video. The
    5 MB image limit must stop the download, not be checked after it. Run under both
    answers to "does github.com accept the token" (live: no for a public repository;
    unverified for a private one)."""
    gh, repo = world.gh, org["repo"]
    gh.behaviour.attachment_token_accepted = token_accepted
    if not token_accepted:
        repo.visibility = "public"
    big = gh.upload(repo, "video.mp4", b"\x00" * (40 * 1024 * 1024), image=True)
    gh.add_issue(repo, "Repro video", body=f"![video]({big})")
    await world.sync()
    await world.stream(f"{repo.id}/issues/1")
    read = sum(n for url, n in gh.bytes_read.items() if "private-user-images" in url)
    assert read <= 6 * 1024 * 1024, f"read {read} bytes"


async def test_attachment_over_the_limit_is_refused_without_reading_it_all(world, org) -> None:
    gh, repo = world.gh, org["repo"]
    repo.visibility = "public"
    big = gh.upload(repo, "huge.zip", b"\x00" * (30 * 1024 * 1024))
    gh.add_issue(repo, "Dump", body=f"[huge.zip]({big})")
    await world.sync()
    key = next(k for k, d in (await world.by_type("FILE")).items() if d.get("recordName") == "huge.zip")
    record = await world.typed(key)
    with pytest.raises(Exception, match="413|limit"):
        response = await world.connector.stream_record(record)
        async for _ in response.body_iterator:
            pass
    read = sum(n for url, n in gh.bytes_read.items() if "private-user-images" in url)
    assert read <= 26 * 1024 * 1024


async def test_unsafe_title_and_filename_in_headers(world, org) -> None:
    gh, repo = world.gh, org["repo"]
    url = gh.upload(repo, "a.txt", b"hello")
    gh.add_issue(repo, 'Quote " and \r\nX-Injected: 1 ünïcødé', body=f'[evil"\r\nX-Bad: 1.txt]({url})')
    await world.sync()
    record = await world.typed(f"{repo.id}/issues/1")
    response = await world.connector.stream_record(record)
    disposition = response.headers["content-disposition"]
    assert "\r" not in disposition and "\n" not in disposition
    for key in (await world.by_type("FILE")):
        if key.startswith("https://"):
            file_record = await world.typed(key)
            response = await world.connector.stream_record(file_record)
            assert "\n" not in response.headers.get("content-disposition", "")


async def test_token_never_appears_in_logs(world, org) -> None:
    gh, repo = world.gh, org["repo"]
    gh.add_issue(repo, "Issue", body=f"![x]({gh.upload(repo, 'x.png', b'png', image=True)})")
    await world.sync()
    gh.sync_token.revoked = True
    await world.sync(expect_error=True)
    await world.restart()
    assert gh.sync_token.value not in world.logs.text()
    assert all(gh.sync_token.value not in r.getMessage() for r in world.logs.records if r.levelno >= logging.DEBUG)
