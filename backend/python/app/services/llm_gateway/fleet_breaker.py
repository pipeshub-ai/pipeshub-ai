"""The fleet-wide circuit breaker of a model provider key, kept in Redis.

Every process and pod of every role shares one state per ``(provider key, size class)``, so an outage seen
by one replica is seen by all. The state machine, the half-open trial grant, the 5-minute success rate that
decides whether a failed send is charged to its job, and the fleet-hold rule all run as Lua on Redis ``TIME``.

* **Open** after 5 consecutive failures, or at least half of the last 20 outcomes (10 or more seen).
* **Half-open trial is a real request.** When the cooldown is over, one waiting job is granted the trial
  and sends its own request. The trial is never charged to the job. Trials rotate over the waiting jobs
  (a job takes one only if it has carried no more than any other waiting job), so a backlog shares them and a
  lone job carries them all. A trial due for more than 300 s and unclaimed goes to any waiting job.
* The cooldown starts at 30 s and doubles to 60 min after each failed trial.
* **Size classes** (small up to 2k estimated tokens, large above): small calls that succeed must not keep a
  breaker closed, or make failing large calls look like a problem of their document.
* **Fleet hold:** three distinct jobs capped on one key within two hours put the key on hold (breaker forced
  open with a 600 s cooldown). A hold ends with the first successful trial.

An unreadable Redis is the gateway's policy, not this module's: it pauses the call and never falls back.
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.services.llm_gateway import fleet_breaker_lua as lua

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.services.llm_gateway.fleet_redis import FleetRedis

SMALL, LARGE = "s", "l"
SMALL_CALL_TOKENS = 2000

BASE_COOLDOWN_MS = 30_000
TRIAL_MAX_MS = 60 * 60 * 1000
TRIAL_LEASE_MS = 60_000
STALE_WAITING_MS = 2 * TRIAL_MAX_MS
UNCLAIMED_TRIAL_MS = 300_000
CONSECUTIVE_TRIP = 5
WINDOW_TRIP_MIN = 10
RATE_MIN_OUTCOMES = 20
RATE_THRESHOLD_PCT = 95
HOLD_WINDOW_MS = 2 * 3600 * 1000
HOLD_JOBS = 3
HOLD_COOLDOWN_MS = 600_000
REDIS_OP_TIMEOUT_S = 2.0

ALLOW, REFUSE, TRIAL = 0, 1, 2


class FleetBreakerUnavailable(Exception):
    """The breaker's Redis could not be read. Paid calls pause; nothing falls back to a local breaker."""


# Redrives a hold owes are kept this long after the last one was added: a job still not redriven by then stays FAILED
# for a user's "Reindex failed".
OWED_TTL_SECONDS = 24 * 3600


def owed_key_name(scope: str) -> str:
    """The set of jobs a hold on ``scope`` (``<pk>:<cls>``) owes a redrive, in the key's own hash tag, so the script that
    opens the hold writes it atomically (namespace it with ``FleetRedis.key``)."""
    return f"sg:{{p:{scope}}}:owed"


def size_class(estimated_tokens: int) -> str:
    return SMALL if estimated_tokens <= SMALL_CALL_TOKENS else LARGE


INSTALL_SECRET_KEY = "/services/indexing/spendGuard/hmacKey"
_install_secret: bytes = b"unbound-install-secret"  # only a process that never bound one (a unit test) uses this


def bind_install_secret(secret: str | bytes) -> None:
    """Set the install secret every process of the install shares (see ``load_install_secret``)."""
    global _install_secret  # noqa: PLW0603 - one secret per install, bound at start-up
    _install_secret = secret.encode() if isinstance(secret, str) else secret


async def load_install_secret(config_service: Any) -> None:  # noqa: ANN401 - ConfigurationService
    """Read the install secret from the configuration service, creating it once if absent, and bind it.

    ``create_config_if_absent`` makes concurrent first starts of several replicas agree on one value, so every process
    derives the same key digests. Raises when the store cannot be read: without the secret the breakers would not be shared."""
    import secrets

    await config_service.create_config_if_absent(INSTALL_SECRET_KEY, secrets.token_hex(32))
    value = await config_service.get_config(INSTALL_SECRET_KEY, use_cache=False, raise_on_error=True)
    if not isinstance(value, str) or len(value) < 32:
        raise RuntimeError(f"the install secret at {INSTALL_SECRET_KEY} is missing or malformed")
    bind_install_secret(value)


_CREDENTIAL_WORDS = ("api_key", "apikey", "secret", "token", "credential", "password", "access_key")
_NESTED = ("client", "_client", "async_client", "root_client", "llm", "_llm", "model", "_model", "bound")


def _aws_identity(credentials: object) -> str:
    """A botocore client's identity: its static key pair, or for credentials that refresh themselves (a role), the
    method and the role ARN. Reading a refreshing credential's keys can call STS, and the keys rotate, so they are not read."""
    if credentials is None:
        return "aws:none"
    refresh = getattr(credentials, "_refresh_using", None)
    if refresh is not None:
        fetcher = getattr(refresh, "__self__", None)
        role = getattr(fetcher, "_role_arn", None) or ""
        return f"aws-role:{getattr(credentials, 'method', '')}:{role}"
    return f"aws:{getattr(credentials, 'access_key', '')}:{getattr(credentials, 'secret_key', '')}"


def _cloud_identity(value: object) -> str | None:
    """The identity of a cloud SDK object that keeps its credentials outside string attributes: a botocore client
    (Bedrock), or a google-auth service-account credentials object (Vertex AI), whose quota is per service account."""
    signer = getattr(value, "_request_signer", None)
    if signer is not None:
        return _aws_identity(getattr(signer, "_credentials", None))
    email = getattr(value, "service_account_email", None)
    if isinstance(email, str) and email:
        return f"gcp:{email}:{getattr(value, 'project_id', None) or ''}"
    return None


def credential_of(model: object) -> str:
    """The credential a model's background requests carry, as one string to feed the key digest (never stored or logged).

    When identity forwarding sends background traffic with the connection's own service key in place of the model's
    (``ConnectionForwardingSettings.background_api_key``), that key is what the provider counts quota and outages by, so it
    alone is the credential. Otherwise it is what the model holds: its own attributes and one level of the clients and
    wrapped models it holds."""
    forwarded = _forwarded_background_key(model)
    if forwarded is not None:
        return f"forwarded={forwarded}"
    found: list[str] = []

    def scan(obj: object, depth: int) -> None:
        sources = [getattr(obj, "__dict__", None), getattr(obj, "__pydantic_private__", None)]
        for source in sources:
            if not isinstance(source, dict):
                continue
            for name, value in list(source.items()):
                lowered = str(name).lower()
                identity = None if isinstance(value, (str, int, float, bool)) or value is None else _cloud_identity(value)
                if identity is not None:
                    found.append(f"{name}={identity}")
                elif any(word in lowered for word in _CREDENTIAL_WORDS):
                    getter = getattr(value, "get_secret_value", None)
                    text = getter() if callable(getter) else value
                    if isinstance(text, str) and text:
                        found.append(f"{name}={text}")
                elif depth == 0 and name in _NESTED and value is not None and not isinstance(value, (str, int, float, bool)):
                    scan(value, depth + 1)

    try:
        scan(model, 0)
    except Exception:
        return ""
    return "|".join(sorted(found))


def _forwarded_background_key(model: object) -> str | None:
    """The service key the identity hook on any of ``model``'s httpx clients puts on background requests."""
    from app.services.model_identity.transport.client_paths import iter_http_clients
    from app.services.model_identity.transport.httpx_hook import background_api_key

    try:
        keys = {key for client in iter_http_clients(model) if (key := background_api_key(client))}
    except Exception:
        return None
    # A model whose clients carry different service keys is keyed by all of them.
    return "|".join(sorted(keys)) if keys else None


# The credential is stretched with PBKDF2 under the install secret, so a digest is costly to test guesses against even
# for someone who has both the digests (Redis keys) and the secret (the configuration store). Computed once per
# credential in a process (cached), so a model call pays nothing for it.
PK_ITERATIONS = 100_000


def provider_pk(provider: str, credential: str = "") -> str:
    """The key every breaker, rate window and hold of a provider key lives under: a PBKDF2-HMAC-SHA256 digest of the
    provider key (class, model and endpoint) and the credential, salted with the install secret. The credential is mixed
    in so two orgs with different API keys on the same model and endpoint do not share an outage or a hold; it is never
    stored in clear or as a plain hash (a fast hash of a short secret can be brute-forced)."""
    return _provider_pk(_install_secret, provider, credential)


@functools.lru_cache(maxsize=1024)
def _provider_pk(secret: bytes, provider: str, credential: str) -> str:
    material = f"{provider}|{credential}".encode()
    return hashlib.pbkdf2_hmac("sha256", material, secret, PK_ITERATIONS).hex()[:24]


@dataclass(frozen=True)
class Admission:
    code: int
    retry_after_s: float = 0.0
    token: str = ""

    @property
    def allowed(self) -> bool:
        return self.code != REFUSE

    @property
    def trial(self) -> bool:
        return self.code == TRIAL


@dataclass(frozen=True)
class Released:
    episode: str
    since_ms: int
    label: str
    cls: str


@dataclass(frozen=True)
class Settlement:
    state: str
    chargeable: bool
    trial_result: str = ""
    released: Released | None = None


@dataclass(frozen=True)
class CappedResult:
    # The job was counted for this revision before: it is refused a second hold.
    already: bool
    distinct: int
    newly_held: bool
    episode: str
    others: tuple[str, ...] = field(default_factory=tuple)


def _s(value: Any) -> str:  # noqa: ANN401 - redis replies are bytes or str by client config
    return value.decode() if isinstance(value, bytes) else str(value)


class FleetBreaker:
    """Calls the scripts. ``clock_ms`` is a test seam (a simulated clock); production reads Redis ``TIME``."""

    def __init__(self, fleet: FleetRedis, *, clock_ms: Callable[[], int] | None = None) -> None:
        self._fleet = fleet
        self._clock_ms = clock_ms or fleet.clock_ms

    @property
    def fleet(self) -> FleetRedis:
        return self._fleet

    def _now(self) -> str:
        return str(self._clock_ms()) if self._clock_ms is not None else ""

    def _key(self, pk: str, cls: str, name: str) -> str:
        return self._fleet.key(f"sg:{{p:{pk}:{cls}}}:{name}")

    def hold_key(self, pk: str, cls: str) -> str:
        return self._key(pk, cls, "hold")

    def ledger_key(self, scope: str) -> str:
        return self._fleet.key(f"sg:hold:ledger:{{{scope}}}")

    async def _run(self, script: str, keys: list[str], *args: object) -> Any:  # noqa: ANN401
        try:
            return await asyncio.wait_for(self._fleet.client().eval(script, len(keys), *keys, *args), REDIS_OP_TIMEOUT_S)
        except Exception as exc:
            raise FleetBreakerUnavailable(f"{type(exc).__name__}: {exc}") from exc

    async def admit(self, pk: str, cls: str, job: str = "") -> Admission:
        """May a call go out? ``job`` is the waiting job's stage-state key ('' for a caller with none)."""
        token = uuid.uuid4().hex
        code, retry_ms, granted = await self._run(
            lua.ADMIT,
            [self._key(pk, cls, "br"), self._key(pk, cls, "waiting"), self._key(pk, cls, "trials")],
            job, self._now(), BASE_COOLDOWN_MS, TRIAL_LEASE_MS, STALE_WAITING_MS, UNCLAIMED_TRIAL_MS, token,
        )
        return Admission(int(code), int(retry_ms) / 1000.0, _s(granted))

    async def settle(self, pk: str, cls: str, outcome: str, *, token: str = "", was_closed: bool = True) -> Settlement:
        """Record a call's outcome: ``ok``, ``ok_unrated`` (the request's fault: the provider answered), ``fail``
        or ``neutral`` (a caller's own budget ran out)."""
        reply = await self._run(
            lua.SETTLE,
            [
                self._key(pk, cls, "br"), self._key(pk, cls, "rate"), self.hold_key(pk, cls),
                self._key(pk, cls, "waiting"), self._key(pk, cls, "trials"),
            ],
            self._now(), outcome, token, "1" if was_closed else "0", BASE_COOLDOWN_MS, TRIAL_MAX_MS,
            RATE_MIN_OUTCOMES, RATE_THRESHOLD_PCT, TRIAL_LEASE_MS, CONSECUTIVE_TRIP, WINDOW_TRIP_MIN,
        )
        state, chargeable, result, episode, since, label, hold_cls = (_s(v) for v in reply)
        released = Released(episode, int(since or 0), label, hold_cls) if episode else None
        return Settlement(state, chargeable == "1", result, released)

    async def renew(self, pk: str, cls: str, token: str) -> bool:
        reply = await self._run(lua.RENEW, [self._key(pk, cls, "br")], self._now(), token, TRIAL_LEASE_MS)
        return int(reply) == 1

    async def register_capped(self, pk: str, cls: str, job: str, *, label: str, reason: str, org: str = "") -> CappedResult:
        reply = await self._run(
            lua.CAPPED,
            [
                self._key(pk, cls, "capped"), self._key(pk, cls, "counted"), self._key(pk, cls, "br"), self.hold_key(pk, cls),
                self._fleet.key(owed_key_name(f"{pk}:{cls}")),
            ],
            self._now(), job, HOLD_WINDOW_MS, HOLD_JOBS, HOLD_COOLDOWN_MS, uuid.uuid4().hex, reason, label, cls,
            OWED_TTL_SECONDS, org,
        )
        already, distinct, newly, episode, *members = reply
        return CappedResult(
            bool(int(already)), int(distinct), bool(int(newly)), _s(episode),
            tuple(m for m in (_s(x) for x in members) if m != job),
        )

    async def hold(self, pk: str, cls: str) -> dict[str, str] | None:
        try:
            found = await asyncio.wait_for(self._fleet.client().hgetall(self.hold_key(pk, cls)), REDIS_OP_TIMEOUT_S)
        except Exception as exc:
            raise FleetBreakerUnavailable(f"{type(exc).__name__}: {exc}") from exc
        return {_s(k): _s(v) for k, v in found.items()} if found else None

    async def keep_hold(self, pk: str, cls: str) -> int:
        """The leader's keep-alive: 1 while the key is held (its breaker is refreshed), 0 when the hold was released here
        because the breaker is no longer open (state lost, or closed by something other than a trial), -1 when no hold exists."""
        held, _ = await self._run(lua.HOLD_KEEP, [self._key(pk, cls, "br"), self.hold_key(pk, cls)])
        return int(held)

    async def advance_ledger(self, scope: str, *, held: bool, since_ms: int = 0) -> int:
        reply = await self._run(
            lua.ADVANCE_LEDGER,
            [self.ledger_key(scope), self._fleet.key(f"sg:hold:ledger:last:{{{scope}}}")],
            self._now(), "1" if held else "0", since_ms,
        )
        return int(reply)

    async def now_ms(self) -> int:
        """Redis ``TIME`` in milliseconds (the injected clock in tests)."""
        if self._clock_ms is not None:
            return int(self._clock_ms())
        try:
            seconds, micros = await asyncio.wait_for(self._fleet.client().time(), REDIS_OP_TIMEOUT_S)
        except Exception as exc:
            raise FleetBreakerUnavailable(f"{type(exc).__name__}: {exc}") from exc
        return int(seconds) * 1000 + int(micros) // 1000

    async def start_ledger(self, scope: str) -> None:
        """A hold begins: the ledger counts from now, not from the last tick of an earlier hold."""
        await self.advance_ledger(scope, held=False)

    async def ledger(self, *scopes: str) -> int:
        """The sum of the hold ledgers of ``scopes`` (a missing ledger is 0)."""
        total = 0
        for scope in scopes:
            try:
                value = await asyncio.wait_for(self._fleet.client().get(self.ledger_key(scope)), REDIS_OP_TIMEOUT_S)
            except Exception as exc:
                raise FleetBreakerUnavailable(f"{type(exc).__name__}: {exc}") from exc
            total += int(value) if value else 0
        return total
