"""DROPBOX_API_BASE_URL: test stacks point the Dropbox SDK at a simulator; nothing else is ever reached with it."""

import pytest

from app.sources.client.dropbox import endpoint


def test_unset_means_dropbox_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(endpoint.ENV, raising=False)
    assert endpoint.dropbox_api_base_url() is None
    assert endpoint.session_for_base_url() is None


@pytest.mark.parametrize("value", ["dropbox-sim", "ftp://x", "http://x/path", "http://u:p@x", "http://x?q=1"])
def test_a_malformed_value_is_refused(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(endpoint.ENV, value)
    with pytest.raises(ValueError):
        endpoint.dropbox_api_base_url()


def test_dropbox_hosts_go_to_the_base_url_with_their_host_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(endpoint.ENV, "http://dropbox-sim.internal")
    session = endpoint.session_for_base_url()
    seen = {}

    def fake(self, method, url, *args, headers=None, **kwargs):  # noqa: ANN202
        seen.update(method=method, url=url, host=headers["Host"])
        return "response"

    monkeypatch.setattr("requests.Session.request", fake)
    assert session.request("POST", "https://api.dropboxapi.com/2/files/list_folder") == "response"
    assert seen == {"method": "POST", "url": "http://dropbox-sim.internal/2/files/list_folder",
                    "host": "api.dropboxapi.com"}


def test_any_other_host_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(endpoint.ENV, "http://dropbox-sim.internal")
    with pytest.raises(ValueError):
        endpoint.session_for_base_url().request("GET", "https://evil.example/steal")
