"""Whether a URL or host is OpenAI's own API, as opposed to a server that only speaks its protocol."""

from urllib.parse import urlparse

OPENAI_API_HOST = "api.openai.com"


def is_openai_api_host(host: str | None) -> bool:
    """True for ``api.openai.com`` and its regional subdomains.

    OpenAI serves data-residency projects from ``us.api.openai.com``,
    ``eu.api.openai.com`` and similar hosts. They are the same API, Responses
    API and token-array embedding input included, so a check that matched only
    ``api.openai.com`` sent gpt-5.x reasoning with tools to Chat Completions there,
    which OpenAI rejects.
    """
    if not host:
        return False
    host = host.lower().removesuffix(".")
    return host == OPENAI_API_HOST or host.endswith(f".{OPENAI_API_HOST}")


def is_openai_api_url(url: str | None) -> bool:
    if not url:
        return False
    try:
        return is_openai_api_host(urlparse(url).hostname)
    except ValueError:
        return False
