"""Validate Slack-hosted file URLs before sending a Slack token."""

from urllib.parse import urlsplit


def slack_file_url(fd: dict) -> str | None:
    if fd.get("is_external") or fd.get("mode") == "external" or fd.get("filetype") == "remote":
        return None
    url = fd.get("url_private_download") or fd.get("url_private")
    if not isinstance(url, str):
        return None
    try:
        parsed = urlsplit(url)
        if (parsed.scheme == "https" and parsed.hostname == "files.slack.com"
                and parsed.port in (None, 443) and parsed.username is None and parsed.password is None):
            return url
    except ValueError:
        pass
    return None
