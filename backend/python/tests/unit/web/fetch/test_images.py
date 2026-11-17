"""Reading a page's images, through the same guarded fetch as pages (in-memory sites only)."""

import base64

import pytest
from web_behaviour_fakes import INTRANET_HOST, FakeWeb, Page

from app.connectors.sources.web.address_guard import ALLOWED_HOSTS_ENV
from app.web.deadline import Deadline
from app.web.errors import TooLarge, UnsafeUrl, UnsupportedContent, WebError
from app.web.fetch.images import MAX_IMAGE_MB, fetch_image_data_uri, image_media_type

SITE = "http://site.test"
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\0" * 64
WEBP = b"RIFF\x24\0\0\0WEBPVP8 " + b"\0" * 64
GIF = b"GIF89a" + b"\0" * 64


def _deadline() -> Deadline:
    return Deadline.after(15)


def _image(body: bytes, content_type: str = "image/png") -> Page:
    return Page(body=body, content_type=content_type)


@pytest.mark.parametrize(("body", "media_type"), [(PNG, "image/png"), (JPEG, "image/jpeg"), (WEBP, "image/webp")])
async def test_an_image_is_read_as_a_data_uri(fake_web: FakeWeb, body: bytes, media_type: str) -> None:
    fake_web.add(f"{SITE}/a", _image(body, media_type))

    uri = await fetch_image_data_uri(f"{SITE}/a", _deadline())

    prefix = f"data:{media_type};base64,"
    assert uri.startswith(prefix)
    assert base64.b64decode(uri[len(prefix):]) == body


async def test_the_bytes_decide_the_type_not_the_header(fake_web: FakeWeb) -> None:
    fake_web.add(f"{SITE}/mislabelled", _image(JPEG, "application/octet-stream"))

    assert (await fetch_image_data_uri(f"{SITE}/mislabelled", _deadline())).startswith("data:image/jpeg;base64,")


@pytest.mark.parametrize(
    "body",
    [b"<html><body>Not found</body></html>", GIF, b""],
    ids=["an error page labelled image/png", "a GIF", "nothing"],
)
async def test_what_is_not_a_png_jpeg_or_webp_is_refused(fake_web: FakeWeb, body: bytes) -> None:
    fake_web.add(f"{SITE}/x", _image(body, "image/png"))

    with pytest.raises(UnsupportedContent):
        await fetch_image_data_uri(f"{SITE}/x", _deadline())


async def test_an_image_past_the_size_limit_is_refused(fake_web: FakeWeb) -> None:
    fake_web.add(f"{SITE}/huge", _image(PNG + b"\0" * (MAX_IMAGE_MB * 1024 * 1024)))

    with pytest.raises(TooLarge):
        await fetch_image_data_uri(f"{SITE}/huge", _deadline())


async def test_an_error_status_is_refused(fake_web: FakeWeb) -> None:
    fake_web.add(f"{SITE}/gone", Page(body=PNG, status=404, content_type="image/png"))

    with pytest.raises(WebError):
        await fetch_image_data_uri(f"{SITE}/gone", _deadline())


async def test_an_image_on_a_private_address_is_never_requested(
    fake_web: FakeWeb, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A page can point its <img> anywhere; even a host the operator lets the web connector
    crawl is off limits here, as it is for pages."""
    monkeypatch.setenv(ALLOWED_HOSTS_ENV, INTRANET_HOST)
    fake_web.add(f"http://{INTRANET_HOST}/secret.png", _image(PNG))

    with pytest.raises(UnsafeUrl):
        await fetch_image_data_uri(f"http://{INTRANET_HOST}/secret.png", _deadline())
    assert fake_web.requests == []


async def test_a_redirect_to_a_private_address_is_refused(fake_web: FakeWeb) -> None:
    fake_web.redirect(f"{SITE}/a.png", f"http://{INTRANET_HOST}/secret.png").add(
        f"http://{INTRANET_HOST}/secret.png", _image(PNG),
    )

    with pytest.raises(UnsafeUrl):
        await fetch_image_data_uri(f"{SITE}/a.png", _deadline())
    assert fake_web.gets(f"http://{INTRANET_HOST}/secret.png") == 0


async def test_the_callers_redirect_check_is_asked(fake_web: FakeWeb) -> None:
    fake_web.redirect(f"{SITE}/a.png", f"{SITE}/b.png").add(f"{SITE}/b.png", _image(PNG))
    asked: list[str] = []

    async def refuse(target: str) -> bool:
        asked.append(target)
        return False

    with pytest.raises(WebError):
        await fetch_image_data_uri(f"{SITE}/a.png", _deadline(), allow_hop=refuse)
    assert asked and fake_web.gets(f"{SITE}/b.png") == 0


@pytest.mark.parametrize(
    ("content", "expected"),
    [(PNG, "image/png"), (JPEG, "image/jpeg"), (WEBP, "image/webp"), (GIF, None), (b"RIFF\0\0\0\0WAVE", None), (b"", None)],
)
def test_image_media_type(content: bytes, expected: str | None) -> None:
    assert image_media_type(content) == expected
