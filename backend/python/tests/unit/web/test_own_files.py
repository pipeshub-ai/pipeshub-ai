import json

import pytest

from app.web.own_files import (
    ISSUED_DOWNLOAD_LINKS_KEY,
    IssuedFile,
    hide_storage_signatures,
    is_signed_storage_link,
    issued_file,
    link_keys,
    remember_download_link,
    signature_removed,
)

# Made-up values in the shape of an S3 presigned link.
S3_LINK = (
    "https://files-bucket.s3.us-west-2.amazonaws.com/org-1/KnowledgeBase/private/doc-1/current/report.pdf"
    "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIAEXAMPLE%2F20261117%2Fus-west-2%2Fs3%2Faws4_request"
    "&X-Amz-Date=20261117T000000Z&X-Amz-Expires=3600&X-Amz-Signature=0123abcd&X-Amz-SignedHeaders=host"
)


class TestLinkKey:
    def test_reordered_and_reencoded_copies_match(self) -> None:
        """The model may decode `%2F` or reorder the query when it copies a link into fetch_url."""
        reordered = (
            "https://FILES-BUCKET.s3.us-west-2.amazonaws.com/org-1/KnowledgeBase/private/doc-1/current/report.pdf"
            "?X-Amz-Signature=0123abcd&X-Amz-SignedHeaders=host&X-Amz-Algorithm=AWS4-HMAC-SHA256"
            "&X-Amz-Credential=AKIAEXAMPLE/20261117/us-west-2/s3/aws4_request"
            "&X-Amz-Date=20261117T000000Z&X-Amz-Expires=3600"
        )
        assert link_keys(reordered) == link_keys(S3_LINK)

    def test_another_signature_is_another_link(self) -> None:
        assert link_keys(S3_LINK.replace("0123abcd", "ffff9999")) != link_keys(S3_LINK)

    def test_another_file_is_another_link(self) -> None:
        unsigned = "https://app.example/api/v1/knowledgeBase/stream/record/rec-1"
        assert link_keys(unsigned.replace("rec-1", "rec-2")) != link_keys(unsigned)

    def test_encoded_and_literal_plus_in_a_signature_match(self) -> None:
        """A base64 signature's `+` is `%2B` in the issued link; a copy may carry it decoded."""
        issued = "https://acct.blob.core.windows.net/c/f.pdf?sv=2023-11-03&sp=r&sig=abc%2Bdef%3D"
        assert link_keys("https://acct.blob.core.windows.net/c/f.pdf?sv=2023-11-03&sp=r&sig=abc+def=") == link_keys(issued)

    def test_an_added_parameter_does_not_hide_a_signed_link(self) -> None:
        """An Azure SAS still opens the blob with extra parameters, which its signature doesn't cover."""
        issued = "https://acct.blob.core.windows.net/c/f.pdf?sv=2023-11-03&sp=r&sig=abc%2Bdef%3D"
        assert link_keys(issued + "&x=1") == link_keys(issued)

    def test_a_decoy_signature_does_not_hide_the_real_one(self) -> None:
        """Azure ignores a parameter it doesn't know, so a made-up signature ahead of `sig` still opens the blob."""
        state: dict = {}
        issued = "https://acct.blob.core.windows.net/c/f.pdf?sv=2023-11-03&sp=r&sig=abc%2Bdef%3D"
        remember_download_link(state, issued, record_id="rec-1", file_name="f.pdf")
        decoyed = "https://acct.blob.core.windows.net/c/f.pdf?x-amz-signature=decoy&sv=2023-11-03&sp=r&sig=abc%2Bdef%3D"
        assert issued_file(state[ISSUED_DOWNLOAD_LINKS_KEY], decoyed) == IssuedFile("rec-1", "f.pdf")

    def test_unsigned_links_compare_whole(self) -> None:
        unsigned = "https://app.example/api/v1/knowledgeBase/stream/record/rec-1"
        assert link_keys(unsigned + "?download=1") != link_keys(unsigned)
        assert link_keys(unsigned.replace("https://app.example", "https://APP.example")) == link_keys(unsigned)


class TestIssuedFile:
    def test_remembered_link_is_found(self) -> None:
        state: dict = {}
        remember_download_link(state, S3_LINK, record_id="rec-1", file_name="report.pdf")
        assert issued_file(state[ISSUED_DOWNLOAD_LINKS_KEY], S3_LINK) == IssuedFile("rec-1", "report.pdf")

    def test_remembering_keeps_earlier_links(self) -> None:
        state: dict = {}
        remember_download_link(state, S3_LINK, record_id="rec-1", file_name="report.pdf")
        other = S3_LINK.replace("doc-1", "doc-2").replace("0123abcd", "4567cdef")
        remember_download_link(state, other, record_id="rec-2", file_name="b.pdf")
        assert issued_file(state[ISSUED_DOWNLOAD_LINKS_KEY], S3_LINK).record_id == "rec-1"
        assert issued_file(state[ISSUED_DOWNLOAD_LINKS_KEY], other).record_id == "rec-2"

    @pytest.mark.parametrize("url", [None, ""])
    def test_no_link_is_not_remembered(self, url: str | None) -> None:
        state: dict = {}
        remember_download_link(state, url, record_id="rec-1", file_name="report.pdf")
        assert ISSUED_DOWNLOAD_LINKS_KEY not in state

    @pytest.mark.parametrize("issued", [None, {}])
    def test_nothing_issued(self, issued: dict | None) -> None:
        assert issued_file(issued, S3_LINK) is None

    def test_link_urlsplit_cannot_read_is_not_issued(self) -> None:
        state: dict = {}
        remember_download_link(state, S3_LINK, record_id="rec-1", file_name="report.pdf")
        assert issued_file(state[ISSUED_DOWNLOAD_LINKS_KEY], "http://[::1") is None


class TestIsSignedStorageLink:
    @pytest.mark.parametrize(
        "url",
        [
            S3_LINK,
            "https://storage.googleapis.com/b/f.pdf?X-Goog-Algorithm=GOOG4-RSA-SHA256&X-Goog-Signature=abcd",
            "https://acct.blob.core.windows.net/c/f.pdf?sv=2023-11-03&se=2026-11-18&sp=r&sig=abc%3D",
        ],
    )
    def test_storage_signatures(self, url: str) -> None:
        assert is_signed_storage_link(url)

    @pytest.mark.parametrize(
        "url",
        [
            "https://docs.python.org/3/search.html?q=classes&page=2",
            "https://shop.example/item?code=123&state=CA&key=blue",
            "https://example.com/page",
            "http://[::1",
        ],
    )
    def test_other_links(self, url: str) -> None:
        assert not is_signed_storage_link(url)


class TestHideStorageSignatures:
    def test_signature_and_credential_are_hidden(self) -> None:
        hidden = hide_storage_signatures(f"Reading {S3_LINK} now")
        assert "0123abcd" not in hidden and "AKIAEXAMPLE" not in hidden
        assert hidden.startswith("Reading https://files-bucket.s3.us-west-2.amazonaws.com/org-1/")
        assert hidden.endswith(" now")

    def test_a_link_inside_json_keeps_the_json_valid(self) -> None:
        text = json.dumps({"download_url": S3_LINK, "file_name": "report.pdf"})
        stored = json.loads(hide_storage_signatures(text))
        assert "0123abcd" not in stored["download_url"] and stored["file_name"] == "report.pdf"

    def test_every_signed_link_in_the_text(self) -> None:
        azure = "https://acct.blob.core.windows.net/c/f.pdf?sv=2023-11-03&sp=r&sig=abc%2Bdef%3D"
        hidden = hide_storage_signatures(f"[a]({S3_LINK}) and [b]({azure})")
        assert "0123abcd" not in hidden and "abc%2Bdef" not in hidden
        assert hidden.endswith(")")

    @pytest.mark.parametrize(
        "name",
        ["Budget (final).pdf", "O'Brien.pdf", "Q3 plan.pdf"],
    )
    def test_a_file_name_that_ends_the_link_early(self, name: str) -> None:
        """Azure decodes the path before it checks `sig`, so the raw link still opens the blob."""
        link = f"https://acct.blob.core.windows.net/c/org/{name}?sv=2023-11-03&sp=r&sig=abc%2Bdef%2Fxy%3D"
        hidden = hide_storage_signatures(f"Reading {link}")
        assert "abc%2Bdef" not in hidden
        assert signature_removed(hidden.removeprefix("Reading "))

    def test_credentials_of_a_link_cut_short_are_hidden(self) -> None:
        link = (
            "https://bucket.s3.amazonaws.com/O'Brien.pdf?X-Amz-Credential=AKIAEXAMPLE"
            "&X-Amz-Security-Token=SESSIONEXAMPLE&X-Amz-Signature=abc123"
        )
        hidden = hide_storage_signatures(f"Reading {link}")
        assert "AKIAEXAMPLE" not in hidden and "SESSIONEXAMPLE" not in hidden and "abc123" not in hidden
        assert "O'Brien.pdf?" in hidden

    def test_google_credentials_of_a_link_cut_short_are_hidden(self) -> None:
        link = "https://storage.googleapis.com/b/O'Brien.pdf?X-Goog-Credential=svc%40p.iam&X-Goog-Signature=abcd1234"
        hidden = hide_storage_signatures(f"Reading {link}")
        assert "svc%40p.iam" not in hidden and "abcd1234" not in hidden

    def test_json_that_spells_the_separator_as_an_escape(self) -> None:
        """Go's JSON encoder writes `&` as `\\u0026`; the link scan stops at its backslash."""
        text = '{"url":"https://acct.blob.core.windows.net/c/f.pdf?sv=2023-11-03\\u0026sp=r\\u0026sig=abc%2Bdef%3D"}'
        hidden = hide_storage_signatures(text)
        assert "abc%2Bdef" not in hidden
        assert json.loads(hidden)["url"].endswith("&sig=[REDACTED]")

    def test_a_link_cut_short_inside_json(self) -> None:
        link = "https://acct.blob.core.windows.net/c/O'Brien.pdf?sv=2023-11-03&sig=abc%2Bdef%3D"
        stored = json.loads(hide_storage_signatures(json.dumps({"url": link})))
        assert stored["url"] == "https://acct.blob.core.windows.net/c/O'Brien.pdf?sv=2023-11-03&sig=[REDACTED]"

    @pytest.mark.parametrize(
        "text",
        [
            "no links here",
            "see https://docs.python.org/3/search.html?q=classes&page=2 for more",
            "https://shop.example/item?code=123&state=CA&key=blue",
            "https://app.example/api/v1/knowledgeBase/stream/record/rec-1",
        ],
    )
    def test_other_text_is_unchanged(self, text: str) -> None:
        assert hide_storage_signatures(text) == text


class TestSignatureRemoved:
    def test_a_saved_link(self) -> None:
        assert signature_removed(hide_storage_signatures(S3_LINK))

    def test_a_saved_link_the_model_re_encoded(self) -> None:
        assert signature_removed(hide_storage_signatures(S3_LINK).replace("[REDACTED]", "%5BREDACTED%5D"))

    @pytest.mark.parametrize("url", [S3_LINK, "https://example.com/page?q=[REDACTED]", "http://[::1"])
    def test_other_links(self, url: str) -> None:
        assert not signature_removed(url)
