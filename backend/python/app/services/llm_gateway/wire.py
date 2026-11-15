"""What actually happened on the wire to a model call.

A provider's health is judged from the HTTP status or the transport error of the request, never from the
exception a LangChain wrapper raises: wrappers name the same failure differently (Mistral's
``HTTPStatusError``, Gemini's ``GoogleAPIError``) and some a caller cannot tell from a bad request.

The gateway opens a ``WireOutcome`` for each call (a contextvar). One hook pair is installed on every
httpx client a model holds: the **request** event marks "sent" and caps the connect timeout, the
**response** event records the status. Bedrock goes through botocore events. Hooks only read the
contextvars and never raise, with one exception: a stage job's ``SendMeter`` refuses a request before it
leaves once the job has no failed sends left (``SendAllowanceSpent``), so an SDK's own retries cannot take a
job past its absolute cap.

Judging a finished call (``WireOutcome.judge``):

* a 2xx is a success, and any other 2xx-with-error the request's fault;
* 429, 5xx (529 included), a connect error, a reset, a hang at the ceiling (sent, no response) are failures;
* another 4xx is the request's fault: the provider answered;
* a caller's own budget running out is neutral.
"""

from __future__ import annotations

import contextlib
import logging
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx

from app.services.model_identity.transport.client_paths import iter_http_clients

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT_CAP_S = 10.0
WIRE_MARKER = "_pipeshub_wire_hook"

OK, OK_UNRATED, FAIL, NEUTRAL, UNSENT = "ok", "ok_unrated", "fail", "neutral", "unsent"
_TOO_MANY_REQUESTS = 429
_SERVER_ERROR = 500


@dataclass
class WireOutcome:
    sent: bool = False
    status: int | None = None
    requests: int = 0
    responses: int = 0
    error: str | None = None
    statuses: list[int] = field(default_factory=list)
    # A request of this call was refused before it left: its job had no failed sends left (``SendMeter``).
    allowance_refused: bool = False

    def judge(self, *, raised: BaseException | None, caller_budget_expired: bool = False) -> str:
        """``ok`` / ``ok_unrated`` (the request's fault) / ``fail`` / ``neutral`` / ``unsent`` (no wire evidence)."""
        if not self.sent:
            return UNSENT
        if self.status is None:
            # Sent and nothing came back: a connect error, a reset, or a hang.
            return NEUTRAL if caller_budget_expired else FAIL
        if self.status == _TOO_MANY_REQUESTS or self.status >= _SERVER_ERROR:
            return FAIL
        if raised is not None and _is_transport_failure(raised):
            # Headers arrived and the body never did.
            return NEUTRAL if caller_budget_expired else FAIL
        if self.status >= 400:
            return OK_UNRATED
        return OK if raised is None else OK_UNRATED


def _is_transport_failure(exc: BaseException) -> bool:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (TimeoutError, ConnectionError, httpx.TransportError)):
            return True
        current = current.__cause__ or current.__context__
    return False


_current: ContextVar[WireOutcome | None] = ContextVar("llm_wire_outcome", default=None)


class SendAllowanceSpent(Exception):
    """Raised by the request hook instead of sending: the stage job has used its failed sends (``SendMeter``)."""


@dataclass
class SendMeter:
    """The failed sends of one stage job's run, and how many more it may make.

    A failed send is a request that did not bring back a usable answer: a failed attempt, or a request an SDK
    retried. ``allowance`` is what the job's pause budget has left (``absolute cap - sends so far``); None is no
    bound. The request hook refuses a request when this run's failed sends, plus the requests the current call
    has already made (each one an SDK retry would supersede), reach it.
    """

    allowance: int | None = None
    failed: int = 0

    def remaining(self, in_call: int = 0) -> int | None:
        if self.allowance is None:
            return None
        return max(0, self.allowance - self.failed - in_call)


_meter: ContextVar[SendMeter | None] = ContextVar("llm_send_meter", default=None)


@contextlib.contextmanager
def metered(meter: SendMeter) -> Iterator[SendMeter]:
    """Count the failed sends of the calls made inside this block against ``meter``."""
    token = _meter.set(meter)
    try:
        yield meter
    finally:
        _meter.reset(token)


def current_meter() -> SendMeter | None:
    return _meter.get()


def admit_request() -> None:
    """Raise ``SendAllowanceSpent`` when the request about to leave would take its job past its allowance."""
    meter = _meter.get()
    if meter is None or meter.allowance is None:
        return
    outcome = _current.get()
    if meter.remaining(outcome.requests if outcome is not None else 0) == 0:
        if outcome is not None:
            outcome.allowance_refused = True
        raise SendAllowanceSpent("the stage job has used its sends; this request was not sent")


@contextlib.contextmanager
def wire_scope() -> Iterator[WireOutcome]:
    """Collect the wire events of the call made inside this block."""
    outcome = WireOutcome()
    token = _current.set(outcome)
    try:
        yield outcome
    finally:
        _current.reset(token)


def current_wire() -> WireOutcome | None:
    return _current.get()


def note_request_sent() -> None:
    outcome = _current.get()
    if outcome is not None:
        outcome.sent = True
        outcome.requests += 1
        # A new request supersedes the last answer: an SDK retry decides the call's outcome.
        outcome.status = None


def note_response(status: int) -> None:
    outcome = _current.get()
    if outcome is not None:
        outcome.status = status
        outcome.responses += 1
        outcome.statuses.append(status)


def note_error(name: str) -> None:
    outcome = _current.get()
    if outcome is not None:
        outcome.error = name


def cap_connect_timeout(request: httpx.Request, cap: float = CONNECT_TIMEOUT_CAP_S) -> None:
    """Whatever per-request timeout the SDK computed, connecting never takes longer than ``cap``."""
    timeout = request.extensions.get("timeout")
    if isinstance(timeout, dict):
        connect = timeout.get("connect")
        timeout["connect"] = cap if connect is None else min(connect, cap)
    else:
        request.extensions["timeout"] = {"connect": cap, "read": None, "write": None, "pool": None}


def _request_hook(request: httpx.Request) -> None:
    admit_request()
    with contextlib.suppress(Exception):
        note_request_sent()
        cap_connect_timeout(request)


def _response_hook(response: httpx.Response) -> None:
    with contextlib.suppress(Exception):
        note_response(response.status_code)


async def _async_request_hook(request: httpx.Request) -> None:
    _request_hook(request)


async def _async_response_hook(response: httpx.Response) -> None:
    _response_hook(response)


def has_wire_hook(client: Any) -> bool:  # noqa: ANN401 - SDK clients are duck-typed across providers
    return getattr(client, WIRE_MARKER, False) is True


def install_wire_hook(client: httpx.Client | httpx.AsyncClient) -> bool:
    """Add the request and response hooks to one httpx client. False when it already has them."""
    if has_wire_hook(client):
        return False
    is_async = isinstance(client, httpx.AsyncClient)
    hooks = client.event_hooks
    hooks["request"] = [*hooks.get("request", []), _async_request_hook if is_async else _request_hook]
    hooks["response"] = [*hooks.get("response", []), _async_response_hook if is_async else _response_hook]
    client.event_hooks = hooks
    setattr(client, WIRE_MARKER, True)
    return True


# ---- finding the clients -------------------------------------------------------------------------

_MAX_DEPTH = 4
# A container bigger than this holds data (embeddings, token ids), not clients.
_MAX_CONTAINER = 64
_ATOMS = (str, bytes, int, float, bool, type(None), type)


def _children(obj: object) -> Iterator[object]:
    """Attribute values of ``obj`` read without calling properties (no lazy build, no I/O)."""
    for source in (getattr(obj, "__dict__", None), getattr(obj, "__pydantic_private__", None),
                   getattr(obj, "__pydantic_extra__", None)):
        if isinstance(source, dict):
            yield from list(source.values())
    if isinstance(obj, (list, tuple, set)) and len(obj) <= _MAX_CONTAINER:
        yield from list(obj)
    elif isinstance(obj, dict) and len(obj) <= _MAX_CONTAINER:
        yield from list(obj.values())


def find_http_clients(model: object, max_depth: int = _MAX_DEPTH) -> list[httpx.Client | httpx.AsyncClient]:
    """Every httpx client reachable from ``model`` within ``max_depth`` attribute hops.

    Reaches the SDK clients (``root_client``, ``_client``), google-genai's
    ``_api_client._async_httpx_client`` and groq's ``client._client._client``.
    """
    # The known paths first: they build the SDK clients that LangChain creates lazily (Anthropic's
    # ``_client`` is a cached property), which a walk of the instance dict cannot see.
    found: list[httpx.Client | httpx.AsyncClient] = list(iter_http_clients(model))
    seen: set[int] = {id(client) for client in found}
    visited: set[int] = set()

    def walk(obj: object, depth: int) -> None:
        if isinstance(obj, _ATOMS) or id(obj) in visited:
            return
        visited.add(id(obj))
        if isinstance(obj, (httpx.Client, httpx.AsyncClient)):
            if id(obj) not in seen:
                seen.add(id(obj))
                found.append(obj)
            return
        if depth >= max_depth:
            return
        for child in _children(obj):
            walk(child, depth + 1)

    walk(model, 0)
    return found


def _boto_clients(model: object) -> list[Any]:
    """botocore clients (``bedrock-runtime``) held by a model or its direct SDK attributes."""
    try:
        from botocore.client import BaseClient
    except ImportError:  # pragma: no cover - botocore is a dependency
        return []
    found: list[Any] = []
    seen: set[int] = set()

    def walk(obj: object, depth: int) -> None:
        if isinstance(obj, _ATOMS) or id(obj) in seen:
            return
        seen.add(id(obj))
        if isinstance(obj, BaseClient):
            found.append(obj)
            return
        if depth >= 2:
            return
        for child in _children(obj):
            walk(child, depth + 1)

    walk(model, 0)
    return found


def _genai_api_clients(model: object) -> list[Any]:
    """google-genai ``BaseApiClient`` objects of a model."""
    try:
        from google.genai._api_client import BaseApiClient
    except ImportError:  # pragma: no cover - google-genai is a dependency
        return []
    found: list[Any] = []
    visited: set[int] = set()

    def walk(obj: object, depth: int) -> None:
        if isinstance(obj, _ATOMS) or id(obj) in visited:
            return
        visited.add(id(obj))
        if isinstance(obj, BaseApiClient):
            found.append(obj)
            return
        if depth >= 3:
            return
        for child in _children(obj):
            walk(child, depth + 1)

    walk(model, 0)
    return found


def route_genai_through_httpx(model: object) -> int:
    """Make google-genai send its async requests through its httpx client.

    With ``aiohttp`` installed (it is a dependency) the SDK sends async requests through aiohttp, where no httpx hook
    sees them: an outage of Gemini would never open the breaker. Handing the SDK its own httpx client as
    ``httpx_async_client`` makes it use that client (``BaseApiClient._use_aiohttp`` is then false), and the hook is on it.
    """
    routed = 0
    for api in _genai_api_clients(model):
        client = getattr(api, "_async_httpx_client", None)
        options = getattr(api, "_http_options", None)
        if client is not None and options is not None and getattr(options, "httpx_async_client", None) is None:
            options.httpx_async_client = client
            routed += 1
    return routed


BOTO_MARKER = "_pipeshub_wire_hook"


def install_boto_hooks(client: Any) -> bool:  # noqa: ANN401 - botocore client
    """Mark the request as sent (``before-send``), record the status (``after-call``) and transport errors
    (``needs-retry``) of a botocore client."""
    if getattr(client, BOTO_MARKER, False) is True:
        return False
    events = client.meta.events

    def before_send(**_: object) -> None:
        admit_request()
        with contextlib.suppress(Exception):
            note_request_sent()

    def after_call(http_response: Any = None, **_: object) -> None:  # noqa: ANN401
        with contextlib.suppress(Exception):
            status = getattr(http_response, "status_code", None)
            if isinstance(status, int):
                note_response(status)

    def needs_retry(caught_exception: BaseException | None = None, **_: object) -> None:
        with contextlib.suppress(Exception):
            if caught_exception is not None:
                note_error(type(caught_exception).__name__)

    events.register("before-send", before_send)
    events.register("after-call", after_call)
    events.register("needs-retry", needs_retry)
    setattr(client, BOTO_MARKER, True)
    return True


# Models that make no network call, so there is no provider to be unavailable.
LOCAL_PROVIDERS = frozenset({"default", "huggingFace", "sentenceTransformers"})


def _is_chat_model(model: object) -> bool:
    """A LangChain chat model (a stand-in object in a test is not one). Only chat models go through the
    gateway, so only they are refused when they cannot be observed; an embeddings model is hooked when it can be."""
    from langchain_core.language_models import BaseChatModel

    return isinstance(model, BaseChatModel)


def _in_paid_indexing_workload() -> bool:
    """Whether the model is being built for background (indexing, extraction, parsing) work that spends on a schedule, as
    opposed to a person's chat or an admin check: only there is an unobservable model refused."""
    from app.services.model_identity.call_context import peek_call_context
    from app.services.model_identity.workload import WorkloadClass

    ctx = peek_call_context()
    return ctx is not None and ctx.workload_class is WorkloadClass.BACKGROUND


_unhooked: dict[tuple[str, str], int] = {}


def note_unhooked(provider: str, model: object) -> None:
    key = (provider, type(model).__name__)
    _unhooked[key] = _unhooked.get(key, 0) + 1


def hook_status() -> dict[str, object]:
    """For ``/health``: the models built with no wire hook. Their outages are judged from the exception the wrapper raised
    (the fallback), which misreads some wrappers, so an operator should know which ones they are."""
    models = [{"provider": p, "model": m, "count": n} for (p, m), n in sorted(_unhooked.items())]
    return {"unhooked": models, "warning": bool(models)}


class WireHookError(RuntimeError):
    """A chat model whose requests the gateway cannot observe. It is refused: its outages would never open a breaker."""


def install_wire_hooks(model: Any, provider: str, *, require: Callable[[], bool] | None = None) -> int:  # noqa: ANN401
    """Install the hooks on every client of ``model``. Returns how many clients were hooked (or already were).

    Raises ``WireHookError`` for a chat model of a remote provider that has no client to hook, when built for a paid
    background workload. Elsewhere such a model is allowed and recorded (``hook_status``).
    """
    if provider in LOCAL_PROVIDERS:
        # Nothing to observe, and nothing worth walking (a local model holds tensors).
        logger.info("model %s (%s) runs locally; no wire to observe", type(model).__name__, provider)
        return 0
    clients: list[Any] = [*find_http_clients(model), *_boto_clients(model)]
    route_genai_through_httpx(model)
    hooked = 0
    for client in clients:
        if isinstance(client, (httpx.Client, httpx.AsyncClient)):
            install_wire_hook(client)
            hooked += 1
        else:
            install_boto_hooks(client)
            hooked += 1
    if hooked == 0 and provider not in LOCAL_PROVIDERS:
        if _is_chat_model(model) and (require is None or require()) and _in_paid_indexing_workload():
            raise WireHookError(
                f"cannot observe the requests of the {provider!r} model {type(model).__name__}: no HTTP client to hook"
            )
        # Allowed: the gateway falls back to the exception the wrapper raised, and /health says so.
        note_unhooked(provider, model)
        logger.warning(
            "model %s (%s) has no HTTP client to observe; its outages are judged from exceptions", type(model).__name__, provider
        )
    return hooked
