import pytest

from app.utils.url_redaction import has_signature_query


@pytest.mark.parametrize("url", [
    "https://a.blob.core.windows.net/c/f?sv=2024&sig=abc%3D&se=1",
    "https://b.s3.amazonaws.com/f?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=ab",
    "https://storage.googleapis.com/b/f?X-Goog-Signature=ab",
    "https://h/f?Signature=ab&Expires=1",
    "https://h/f#sig=abc",
])
def test_signed(url: str) -> None:
    assert has_signature_query(url)


@pytest.mark.parametrize("url", [
    "",
    "https://example.com/f.csv",
    "http://localhost:3000/api/v1/document/abc/download",
    "https://example.com/sig=abc/f",
    "https://example.com/f?signed=1&sigma=2&design=3",
])
def test_not_signed(url: str) -> None:
    assert not has_signature_query(url)
