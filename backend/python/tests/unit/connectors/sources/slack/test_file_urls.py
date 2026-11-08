import pytest

from app.connectors.sources.slack.common.file_urls import slack_file_url


@pytest.mark.parametrize("url", [
    "http://files.slack.com/file",
    "https://files.slack.com.attacker.example/file",
    "https://files.slack.com@attacker.example/file",
    "https://attacker.example/file",
    "https://files.slack.com:444/file",
    "https://files.slack.com:bad/file",
])
def test_rejects_untrusted_urls(url) -> None:
    assert slack_file_url({"url_private": url}) is None


def test_accepts_only_slack_hosted_file() -> None:
    url = "https://files.slack.com/files-pri/T-F/file"
    assert slack_file_url({"url_private": url}) == url
    assert slack_file_url({"url_private": url, "is_external": True}) is None
    assert slack_file_url({"url_private": url, "mode": "external"}) is None
    assert slack_file_url({"url_private": url, "filetype": "remote"}) is None
