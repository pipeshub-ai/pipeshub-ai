"""OpenAI endpoint rules: which hosts are OpenAI's own API, and what a configured base URL may contain."""

import re
from collections.abc import Mapping
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


_WRONG_HOST_CODE = "incorrect_hostname"
_NAMED_HOST = re.compile(r"request to ([a-z0-9.-]+)", re.IGNORECASE)


def _error_fields(source: object) -> tuple[object, object]:
    """``(code, message)`` from an OpenAI SDK error or a parsed error payload."""
    body = getattr(source, "body", source)
    if isinstance(body, Mapping):
        inner = body.get("error")
        if isinstance(inner, Mapping):
            body = inner
        return body.get("code") or getattr(source, "code", None), body.get("message")
    return getattr(source, "code", None), getattr(source, "message", None)


def openai_wrong_host(source: object) -> str | None:
    """The host OpenAI says to use when a key reached another region's host, else None.

    OpenAI answers a data-residency project's key on ``api.openai.com`` (or a
    regular key on ``us.api.openai.com``) with 401 ``incorrect_hostname``, which
    otherwise reads as a bad key. Returns "" when OpenAI names no host of its own,
    so a provider's text never reaches the admin unchecked.
    """
    seen: set[int] = set()
    current = source
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        code, message = _error_fields(current)
        if code == _WRONG_HOST_CODE:
            match = _NAMED_HOST.search(message) if isinstance(message, str) else None
            host = match.group(1).rstrip(".").lower() if match else ""
            return host if is_openai_api_host(host) else ""
        current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
    return None


def openai_wrong_host_message(wrong_host: str, called_url: str | None, key_label: str = "this API key") -> str:
    """What to tell the admin after OpenAI's ``incorrect_hostname``.

    *called_url* is the endpoint the request went to; empty means OpenAI's
    default host. A gateway (LiteLLM, a corporate proxy) passes OpenAI's error
    on unchanged, and there the gateway's own OpenAI setting is wrong:
    pointing PipesHub straight at OpenAI would bypass the gateway.
    """
    region = wrong_host or "a different regional host"
    base = f"https://{wrong_host}/v1" if wrong_host else "that region's host"
    if not called_url or is_openai_api_url(called_url):
        return (
            f"OpenAI serves {key_label} from {region}, not from this endpoint. "
            f"Change the Base URL to {base} in Workspace > AI Models, then try again."
        )
    return (
        f"The service at this Base URL forwards requests to OpenAI, which serves that service's "
        f"OpenAI key from {region}. Set the service's OpenAI base URL to {base} and keep this Base URL as it is."
    )


# Providers whose endpoint the OpenAI SDK takes as base_url and appends the
# operation path to. A pasted full URL (".../v1/responses") doubles that path,
# and the provider's 404 then reads as "model not found".
OPENAI_SDK_BASE_URL_PROVIDERS = frozenset({
    "openAI", "openAICompatible", "litellmProxy", "lmStudio", "together",
})
_OPENAI_OPERATION_PATHS = (
    "/chat/completions", "/completions", "/responses", "/embeddings", "/models",
    "/images/generations", "/audio/speech", "/audio/transcriptions",
)


def endpoint_path_problem(provider: str | None, endpoint: object) -> str | None:
    """Why *endpoint* is not a usable base URL for *provider*, or None when it is."""
    if provider not in OPENAI_SDK_BASE_URL_PROVIDERS or not isinstance(endpoint, str):
        return None
    endpoint = endpoint.strip()
    try:
        path = urlparse(endpoint).path.rstrip("/").lower()
    except ValueError:
        return None
    suffix = next((s for s in _OPENAI_OPERATION_PATHS if path.endswith(s)), None)
    if suffix is None:
        return None
    base = endpoint[: endpoint.lower().rindex(suffix)]
    return (
        f"The endpoint {endpoint!r} includes the API path \"{suffix}\". Enter only the base URL, "
        f"{base!r}, in Workspace > AI Models; the path is added for each request."
    )
