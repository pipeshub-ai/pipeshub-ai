"""``repository_fetch`` (WP-63 part A): a public repository an engine run asks for, cloned by
the worker after the person allows it, never by the sandbox.

Git hosts are never reachable from a sandbox (the egress proxy refuses them as websites and refuses git's smart-HTTP at
every site). An engine that needs a repository runs ``ph git clone <https url>``, which calls this tool through the
Tool Gateway. Then:

1. **Which hosts**: only an ``https://`` URL of one repository on an allowed host, optionally limited to owner or
   ``owner/repo`` prefixes. A run with web access gets the release's default presets (github.com, gitlab.com and
   bitbucket.org public repos, ``WEBSITE_RULES.repositoryPresets``), as public web pages are (ADR-REPO-01), each held
   by the deployment's cap (``HARNESS_ALLOWED_REPOSITORIES_CAP``); a run without web access gets none. An edition's
   layers may fold an org's and its agents' lists in their place (``app.harness.org_network_layers``). The hosts are
   read from the org's live layer at each call (``live_repository_hosts``).
2. **The person decides every time** (``RepositoryAsks``): the call waits on the existing approval card, inbox and
   link paths, an unattended run waiting as approvals already do. "Allow for this conversation" holds for this
   repository only (the conversation grant's destination key is the repository), so a later ``ph git fetch`` of it
   runs and any other repository asks again. A conversation that read private data says so on the card (the flow
   rule's reason), and a ``deny`` from the classification extension point refuses the fetch.
3. **The fetch** runs here in the worker (``harness_core.repos.fetch``): resolved once and held to that address, no
   redirects, the hardened git config, a size cap, a time limit, a bare shallow clone, no credential ever. Private
   repositories go only through the run's connected repository (GIT-FLOW).
4. **The hand-off**: the bare repository, as a tar from the deployment's bounded cache, is seeded read-only into the
   turn's session folder; ``ph`` clones it into the workspace inside the sandbox, so a filter or a link in the
   repository acts only there.
5. **Accounting**: what was fetched over the network counts against the run's egress cap at the egress proxy (the
   alias ``other``), and the call journals ``x.pipeshub.repository.fetched`` (host, path, bytes) for the run's
   activity and the audit chain, where Node accepts it.

Nothing here can push: the tool only fetches, and the sandbox's copy has no credential and no route to the host.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Protocol, cast

from app.harness.org_network_layers import org_network_layers
from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects.flow import (
    AskDestination,
    destination_key,
    destination_wire,
    explained_ask,
    grant_answerable,
)
from harness_core.effects.policy import stricter
from harness_core.effects.redaction import redaction_of
from harness_core.effects.types import (
    POLICY_DENIAL,
    ExplainsAsk,
    ExplainsDenial,
    ToolEffect,
    ToolExecution,
    refresh_policy,
    tool_id_for,
)
from harness_core.journal.types import NewEvent
from harness_core.kernel.core.tool_schema import ToolSchema
from harness_core.logs import HarnessLog
from harness_core.provenance.repository import repository_label
from harness_core.provenance.rules import (
    NO_CONTENT_RULE,
    REPOSITORY_RULE,
    ProvenanceRule,
    require_rules,
)
from harness_core.provenance.types import RepositoryRef, ResultProvenance
from harness_core.repos.fetch import MIB, FetchConfig, RepositoryFetcher
from harness_core.repos.location import (
    RepositoryLocation,
    RepositoryRefused,
    allowed_entry,
    check_ref,
    parse_repository_url,
)
from harness_core.sandbox.egress.policy import is_host_name
from harness_core.security.reachability import UNREADABLE, PublicReachability

if TYPE_CHECKING:
    from pydantic import JsonValue

    from app.harness.run_policy.ports import RunPolicy
    from harness_core.effects.flow import ConversationGrants
    from harness_core.effects.redaction import Redaction
    from harness_core.effects.types import ApprovalDecision, ApprovalPolicy, ToolContext
    from harness_core.engines.git_checkout import GitCheckout
    from harness_core.invoker.ports import ToolCatalog
    from harness_core.kernel.core.messages import ToolCall
    from harness_core.provenance.ledger import ProvenanceLedger
    from harness_core.provenance.repository import RepositoryTree
    from harness_core.repos.cache import RepositoryCache
    from harness_core.sandbox.egress.policy import Network
    from harness_core.security.labels import SourceLedger
    from harness_core.workspace.sync.storage_classes import Reference

REPOSITORY_FETCH: Final = "repository_fetch"
TOOL_ID: Final = tool_id_for(REPOSITORY_FETCH, source="pipeshub", instance="git")
# GIT-FLOW's push of the session branch, which a person now confirms too (WP-63 rule).
REPOSITORY_PUSH: Final = "repository_push"
PUSH_TOOL_ID: Final = tool_id_for(REPOSITORY_PUSH, source="pipeshub", instance="git")
FETCHED_EVENT: Final = "x.pipeshub.repository.fetched"
# The card's rule: an engine run's own rule for this tool (the layer "engine"), which asks before every clone.
ASK_RULE: Final = "tool"
ASK_LAYER: Final = "engine"
_LOG = HarnessLog.named("app.harness.repositories")
_HOST: Final = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?")
_PRIVATE_SUFFIXES: Final = (
    "localhost", "local", "internal", "intranet", "lan", "home", "corp", "private", "arpa", "test", "invalid",
    "example", "onion",
)
_CHUNK: Final = 1024 * 1024

ENV_CACHE_DIR: Final = "HARNESS_REPO_CACHE_DIR"
ENV_CACHE_MAX_MB: Final = "HARNESS_REPO_CACHE_MAX_MB"
ENV_REPO_MAX_MB: Final = "HARNESS_REPO_MAX_MB"
ENV_CONCURRENCY: Final = "HARNESS_REPO_FETCH_CONCURRENCY"
ENV_TIMEOUT_S: Final = "HARNESS_REPO_FETCH_TIMEOUT_S"
ENV_MAX_AGE_S: Final = "HARNESS_REPO_CACHE_MAX_AGE_S"
ENV_GIT_BIN: Final = "HARNESS_REPO_GIT_BIN"
ENV_CA_FILE: Final = "HARNESS_REPO_CA_FILE"
ENV_MIN_FREE_MB: Final = "HARNESS_REPO_CACHE_MIN_FREE_MB"
DEFAULT_CACHE_DIR: Final = "/data/pipeshub/harness/repository-cache"

Host = Mapping[str, "JsonValue"]


# --- policy ---------------------------------------------------------------------------------------------------------


def listed_hosts(section: JsonValue | None) -> list[Host] | None:
    """A ``repositories`` section's hosts; ``None``: the section lists none."""
    if not isinstance(section, Mapping):
        return None
    hosts = cast("Mapping[str, JsonValue]", section).get("hosts")
    return [cast("Host", h) for h in hosts if isinstance(h, Mapping)] if isinstance(hosts, list) else None


def _norm(prefix: str) -> str:
    return prefix.lower().rstrip("/")


def prefix_within(inner: str, outer: str) -> bool:
    """Whether a prefix stays inside another: an owner holds that owner and its repositories; a repository holds only
    itself."""
    i, o = _norm(inner), _norm(outer)
    if "/" not in o:
        return i == o or i.startswith(f"{o}/")
    return i == o


def hosts_held_by(entry: Host, above: Sequence[Host]) -> bool:
    """Whether a layer above holds ``entry``: the same host, and its prefixes within that layer's own."""
    inner = entry.get("prefixes")
    for outer_entry in above:
        if str(outer_entry.get("host")) != str(entry.get("host")):
            continue
        outer = outer_entry.get("prefixes")
        if not isinstance(outer, list):
            return True
        if isinstance(inner, list) and all(
            isinstance(p, str) and any(isinstance(o, str) and prefix_within(p, o) for o in outer) for p in inner
        ):
            return True
    return False


def default_repository_policy() -> dict[str, JsonValue]:
    """The section a run with web access gets (ADR-REPO-01): the release's presets marked ``default``, one entry each,
    as a preset is saved. The same as Node's ``defaultRepositoryPolicy``."""
    from harness_core.contracts.generated.policy_data import WEBSITE_RULES

    presets = WEBSITE_RULES.repository_presets or []
    return {"hosts": [{"host": p.host, "preset": p.id} for p in presets if p.default]}


def capped_hosts(deployment: JsonValue | None, section: JsonValue | None) -> list[Host]:
    """``section``'s hosts, each held by the deployment's cap when it sets one (an empty cap holds none)."""
    cap = listed_hosts(deployment)
    return [h for h in listed_hosts(section) or [] if cap is None or hosts_held_by(h, cap)]


def live_repository_hosts(
    repositories: Mapping[str, JsonValue] | None, agent_key: str | None, *, web: bool
) -> list[Host]:
    """The run's git hosts from the org's live layer ``repositories`` member, each checked again. ``web``: the run
    has web access, which is what the release's default presets ride on (ADR-REPO-01)."""
    section: Mapping[str, JsonValue] = repositories or {}
    default = default_repository_policy() if web else None
    layers = org_network_layers()
    folded = (
        capped_hosts(section.get("deployment"), default)
        if layers is None
        else layers.repository_hosts(section, agent_key, default)
    )
    return [h for h in folded if host_ok(str(h.get("host", "")))]


def repository_instructions(hosts: Sequence[Host]) -> str:
    """WP-70: what the engine is told when its run may clone public repositories: the hosts and that ``ph git
    clone`` is the way to read their code. A run that had github.com allowed, never learned the command, and read
    GitHub one page at a time (37 pages, 15 refused API calls). "" when no host is allowed."""
    names = sorted({str(h.get("host")) for h in hosts if isinstance(h.get("host"), str) and h.get("host")})
    if not names:
        return ""
    github = _GITHUB in names
    example = _GITHUB if github else names[0]
    return (
        f"\n\nPublic code: this task may clone public repositories from {', '.join(names)}. To read a repository, "
        f"run `ph git clone https://{example}/<owner>/<repo>`; PipesHub fetches it after the person allows it and "
        "hands you a read-only copy. When the task asks you to check out, read or compare a code repository, clone it "
        "and read its files locally, rather than reading its web pages one by one or calling its API, which refuses "
        "anonymous requests after a few. For one file, fetch its raw URL"
        + (" (https://raw.githubusercontent.com/<owner>/<repo>/<branch>/<path>)" if github else "")
        + " with the web fetch tool. If the person declines a clone or it is refused, say in your answer that you "
        "could not read that repository's code and why; never answer as if you had read it."
    )


# R41-CLONE: the forges a person names a repository on without saying "git host"; any host the run allows counts too.
_GITHUB: Final = "github.com"
KNOWN_FORGES: Final = (_GITHUB, "gitlab.com", "bitbucket.org", "codeberg.org")
# A forge's own pages under its first path segment, never an owner (github.com/features/copilot is no repository).
_FORGE_PAGES: Final = frozenset({
    "about", "apps", "collections", "customer-stories", "enterprise", "explore", "features", "login", "marketplace",
    "orgs", "pricing", "search", "settings", "site", "sponsors", "topics", "trending", "users", "help", "-",
})
_NAMED: Final = re.compile(r"(?<![\w@/.-])(?:https?://)?(?:www\.)?([a-z0-9.-]+\.[a-z]{2,})/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)")
_MAX_NAMED: Final = 10


@dataclass(frozen=True, slots=True)
class NamedRepository:
    """A repository the person's task names (``github.com/acme/widgets``), and whether this run may clone it."""

    location: RepositoryLocation
    allowed: bool

    @property
    def label(self) -> str:
        return self.location.label


def named_repositories(text: str, hosts: Sequence[Host]) -> tuple[NamedRepository, ...]:
    """The repositories ``text`` names on a known forge or on a host the run allows, in order, once each. A link deeper
    into one (``/blob/main/x.py``, ``/issues``) names that repository."""
    allowed_hosts = {str(h.get("host")) for h in hosts if isinstance(h.get("host"), str)}
    forges = allowed_hosts | set(KNOWN_FORGES)
    found: dict[str, NamedRepository] = {}
    for match in _NAMED.finditer(text):
        host, owner, name = match.group(1).lower(), match.group(2), match.group(3).rstrip(".").removesuffix(".git")
        if host not in forges or owner.lower() in _FORGE_PAGES or not name:
            continue
        try:
            location = parse_repository_url(f"https://{host}/{owner}/{name}")
        except RepositoryRefused:
            continue
        key = location.label.lower()
        if key not in found:
            found[key] = NamedRepository(location, allowed_entry(location, hosts) is not None)
        if len(found) == _MAX_NAMED:
            break
    return tuple(found.values())


def named_repositories_line(named: Sequence[NamedRepository]) -> str:
    """The task's own line on the repositories it names (R41-CLONE): each to clone by its command, and those this run
    may not clone, to say so. "" when it names none. Told only "may clone public repositories from
    github.com" in its instructions, a Codex job asked to "check the GitHub code repo" of two named repositories read
    their web pages and never cloned either."""
    if not named:
        return ""
    clone = [r for r in named if r.allowed]
    out = []
    if clone:
        commands = "; ".join(f"`ph git clone {r.location.shown_url}`" for r in clone)
        out.append(
            f"Code repositories this task names: {', '.join(r.label for r in clone)}. Read their code from clones, not "
            f"from their web pages: run {commands}. PipesHub asks the person first; keep working while it waits, then "
            "read the files you need from the clone. If a clone is declined or fails, say in your answer that you could "
            "not read that repository's code."
        )
    refused = [r.label for r in named if not r.allowed]
    if refused:
        out.append(
            f"This task may not clone {', '.join(refused)}: its host is not one of the git hosts it may clone from. Say "
            "in your answer that you could not read that code."
        )
    return " ".join(out)


def host_ok(host: str, private_names: frozenset[str] | None = None) -> bool:
    """The rules Node holds a save to, held again: a lower-case name (never an address or a wildcard), not private or
    internal unless the deployment's ``EGRESS_PRIVATE_ALLOW`` names it."""
    if not _HOST.fullmatch(host) or not is_host_name(host):
        return False
    if any(host == s or host.endswith(f".{s}") for s in _PRIVATE_SUFFIXES):
        from app.harness.websites import private_allow_names

        return host in (private_names if private_names is not None else private_allow_names())
    return True


# --- the deployment's fetcher ---------------------------------------------------------------------------------------


def _number(env: Mapping[str, str], name: str, fallback: float, *, minimum: float = 1) -> float:
    raw = env.get(name, "").strip()
    if not raw:
        return fallback
    try:
        value = float(raw)
    except ValueError:
        _LOG.warning("repository.config_invalid", setting=name)
        return fallback
    return value if value >= minimum else fallback


def fetch_config_from_env(env: Mapping[str, str] | None = None) -> FetchConfig:
    """``HARNESS_REPO_*``: 2 GB of cache (least recently used out first), 500 MB a repository, 2 fetches at once."""
    source = env if env is not None else {
        ENV_CACHE_DIR: os.environ.get(ENV_CACHE_DIR, ""),
        ENV_CACHE_MAX_MB: os.environ.get(ENV_CACHE_MAX_MB, ""),
        ENV_REPO_MAX_MB: os.environ.get(ENV_REPO_MAX_MB, ""),
        ENV_CONCURRENCY: os.environ.get(ENV_CONCURRENCY, ""),
        ENV_TIMEOUT_S: os.environ.get(ENV_TIMEOUT_S, ""),
        ENV_MAX_AGE_S: os.environ.get(ENV_MAX_AGE_S, ""),
        ENV_GIT_BIN: os.environ.get(ENV_GIT_BIN, ""),
        ENV_CA_FILE: os.environ.get(ENV_CA_FILE, ""),
        ENV_MIN_FREE_MB: os.environ.get(ENV_MIN_FREE_MB, ""),
    }
    ca = source.get(ENV_CA_FILE, "").strip() or None
    return FetchConfig(
        cache_dir=Path(source.get(ENV_CACHE_DIR, "").strip() or DEFAULT_CACHE_DIR),
        git_bin=source.get(ENV_GIT_BIN, "").strip() or "/usr/bin/git",
        cache_max_bytes=int(_number(source, ENV_CACHE_MAX_MB, 2048) * MIB),
        repo_max_bytes=int(_number(source, ENV_REPO_MAX_MB, 500) * MIB),
        concurrency=int(_number(source, ENV_CONCURRENCY, 2)),
        timeout_s=_number(source, ENV_TIMEOUT_S, 300),
        max_age_s=_number(source, ENV_MAX_AGE_S, 600, minimum=0),
        ca_file=ca,
        min_free_bytes=_min_free(source),
    )


def _min_free(env: Mapping[str, str]) -> int | None:
    """``HARNESS_REPO_CACHE_MIN_FREE_MB`` in bytes; unset or unreadable: the bounded default reserve."""
    raw = env.get(ENV_MIN_FREE_MB, "").strip()
    value = _number(env, ENV_MIN_FREE_MB, -1, minimum=0) if raw else -1
    return None if value < 0 else int(value * MIB)


_FETCHERS: dict[str, RepositoryFetcher] = {}


def deployment_fetcher() -> RepositoryFetcher:
    """The worker's one fetcher: one cache and one concurrency limit for every run it serves."""
    if "deployment" not in _FETCHERS:
        from app.harness.sandbox.egress_resolver import PublicHostResolver
        from harness_core.sandbox.egress.upstream_proxy import upstream_egress_from_env

        private = upstream_egress_from_env().private_allow
        _FETCHERS["deployment"] = RepositoryFetcher(
            fetch_config_from_env(),
            PublicHostResolver(allow_private=bool(private)),
            private_allow=private,
            refused_networks=deployment_refused_networks(),
            log=_LOG,
        )
    return _FETCHERS["deployment"]


def deployment_refused_networks() -> tuple[Network, ...]:
    """PipesHub's own ranges the proxy refuses (``EGRESS_BLOCKED_CIDRS``) and the sandboxes' pool: never a git host,
    and never public content."""
    from harness_core.sandbox.egress.control import sandbox_subnet_pool_from_env
    from harness_core.sandbox.egress.policy import parse_networks

    return parse_networks([*os.getenv("EGRESS_BLOCKED_CIDRS", "").split(","), sandbox_subnet_pool_from_env()])


def org_reachability(policy: RunPolicy) -> Callable[[], Awaitable[PublicReachability]]:
    """The run's ``PublicReachability``, read at each call from its policy's org-only addresses (WP83-FU-3); a policy
    with no live layer declares none. The member is bound here, not looked up with a default: a policy (an edition's
    wrapper) without it fails the run's build instead of treating every org-only address as public."""
    refused = deployment_refused_networks()
    read = policy.org_only_addresses

    async def reachability() -> PublicReachability:
        return PublicReachability.of(await read(), refused)

    return reachability


# --- the card -------------------------------------------------------------------------------------------------------


def repository_of(call: ToolCall) -> RepositoryLocation | None:
    try:
        return parse_repository_url(call.arguments.get("url"))
    except RepositoryRefused:
        return None


def repository_key(location: RepositoryLocation) -> str:
    """The conversation grant's destination: the repository, whatever ref or depth a later call asks for."""
    return destination_key(TOOL_ID, f"repository={location.host}/{location.path.lower()}")


def repository_destination(call: ToolCall, effect: ToolEffect) -> AskDestination | None:
    """``pipeshub_destination`` for this tool: the repository as the card names it, keyed by the repository."""
    if effect.tool_id != TOOL_ID:
        return None
    location = repository_of(call)
    return None if location is None else AskDestination(location.label, repository_key(location))


def push_destination(checkout: GitCheckout) -> AskDestination:
    """Where a push goes, as the card names it (``pipeshub/session-… on github.com/acme/widgets``): the branch and the
    repository, which a conversation grant is held to."""
    where = f"{checkout.host}/{checkout.repository}"
    return AskDestination(
        f"{checkout.session_branch} on {where}", destination_key(PUSH_TOOL_ID, f"branch={where.lower()}:{checkout.session_branch}")
    )


def card_label(location: RepositoryLocation, size: int | None, cap_bytes: int) -> str:
    """``github.com/acme/widgets (public, read-only, about 42 MB)``: what the title "Clone …?" names. The size is
    the last fetch's when the deployment has one; otherwise the most a clone may bring."""
    amount = (
        f"about {max(1, round(size / MIB))} MB" if size is not None else f"up to {max(1, cap_bytes // MIB)} MB"
    )
    return f"{location.label} (public, read-only, {amount})"


class HoldsReferences(Protocol):
    """The session's references from the turn that runs now (ADR-RUN-01 D2): ``reference_for`` an address an earlier
    turn fetched, and ``fetched`` to vouch for a repository this turn fetched (the shelf relay's)."""

    def reference_for(self, address: str) -> Reference | None: ...

    def fetched(self, address: str) -> None: ...


@dataclass
class RepositoryAsks:
    """``base`` with a person asked before every ``repository_fetch``, unless the
    conversation's grant already holds that repository. A base that refuses still refuses, and a base that asks with
    its own reason (the flow rule after a private read, the classification) keeps it, with the repository named."""

    base: ApprovalPolicy
    ledger: SourceLedger
    run_id: str
    grants: ConversationGrants | None
    size_of: Callable[[RepositoryLocation, ToolCall], int | None]
    cap_bytes: int
    # The run's pushable repository (GIT-FLOW): its ``repository_push`` asks the same way, keyed by its branch.
    push: GitCheckout | None = None
    log: HarnessLog = field(default_factory=lambda: _LOG)
    # ADR-RUN-01 D2: a repository the session holds as a reference is fetched again under the approval it was first
    # fetched with (a later turn re-materialises it); the base's own reasons to ask still hold.
    references: HoldsReferences | None = None
    # The run's git hosts (the tool's own), read at each decision's refresh: a clone of a host they do not allow is
    # refused by the tool with the "Clone not available" card, so the person is never asked to approve it first.
    hosts: Callable[[], Awaitable[list[Host]]] | None = None
    _allowed: list[Host] | None = field(default=None, init=False)

    def _where(self, call: ToolCall, effect: ToolEffect) -> AskDestination | None:
        if effect.tool_id == PUSH_TOOL_ID:
            return push_destination(self.push) if self.push is not None else None
        location = repository_of(call)
        if location is None:
            return None
        return AskDestination(card_label(location, self.size_of(location, call), self.cap_bytes), repository_key(location))

    def _own(self, call: ToolCall, effect: ToolEffect) -> ApprovalDecision:
        if effect.tool_id not in (TOOL_ID, PUSH_TOOL_ID):
            return "allow"
        where = self._where(call, effect)
        # A URL the tool refuses anyway runs (and is refused there), rather than asking a person about nothing.
        if where is None:
            return "allow"
        if self.grants is not None and self.grants.allows(effect.tool_id, where.key):
            self.log.info("repository.grant_held", toolId=effect.tool_id)
            return "allow"
        location = repository_of(call) if effect.tool_id == TOOL_ID else None
        if location is not None and self._allowed is not None and allowed_entry(location, self._allowed) is None:
            return "allow"
        if location is not None and self.references is not None and self.references.reference_for(location.shown_url):
            self.log.info("repository.reference_held", toolId=effect.tool_id)
            return "allow"
        return "ask"

    async def refresh(self) -> None:
        await refresh_policy(self.base)
        if self.hosts is None:
            return
        try:
            self._allowed = await self.hosts()
        except Exception as exc:  # unread hosts ask as before: the tool still checks them at its call
            self._allowed = None
            self.log.warning("repository.hosts_unread", errorType=type(exc).__name__)

    def __call__(self, call: ToolCall, effect: ToolEffect) -> ApprovalDecision:
        return stricter(self.base(call, effect), self._own(call, effect))

    def ask_reason(self, call: ToolCall, effect: ToolEffect) -> Mapping[str, JsonValue] | None:
        own = self._own(call, effect)
        base = self.base(call, effect)
        reason = self.base.ask_reason(call, effect) if base == "ask" and isinstance(self.base, ExplainsAsk) else None
        if effect.tool_id not in (TOOL_ID, PUSH_TOOL_ID):
            return reason
        where = self._where(call, effect)
        if reason:
            return {**reason, "destination": destination_wire(where)} if where is not None else reason
        if own != "ask":
            return None
        return explained_ask(
            ASK_RULE, effect, self.ledger, self.run_id, where, grantable=grant_answerable(self.base, call, effect),
            layer=ASK_LAYER,
        )

    def grant_answers(self, call: ToolCall, effect: ToolEffect) -> bool:
        return self.base(call, effect) != "deny" and grant_answerable(self.base, call, effect)

    def redaction(self, call: ToolCall, effect: ToolEffect) -> Redaction | None:
        return redaction_of(self.base, call, effect)

    def denial(self, call: ToolCall, effect: ToolEffect) -> tuple[str, str]:
        return self.base.denial(call, effect) if isinstance(self.base, ExplainsDenial) else POLICY_DENIAL


# --- the tool -------------------------------------------------------------------------------------------------------


class VolumeAccount(Protocol):
    """The run's outside-traffic cap at the egress proxy: ``(allowed, remaining)`` after counting ``received``."""

    async def charge(self, *, org_id: str, run_id: str, received: int) -> tuple[bool, int | None]: ...


class HandsOff(Protocol):
    async def hand_off(self, archive: AsyncIterator[bytes], size_bytes: int) -> str | None: ...


async def file_chunks(path: Path) -> AsyncIterator[bytes]:
    handle = await asyncio.to_thread(path.open, "rb")
    try:
        while chunk := await asyncio.to_thread(handle.read, _CHUNK):
            yield chunk
    finally:
        await asyncio.to_thread(handle.close)


def repository_effect() -> ToolEffect:
    """A read whose request goes to an outside host the model named (FG2): the flow rule holds it after a private
    read, and a classification that forbids egress refuses it. Fetching again changes nothing: idempotent."""
    return ToolEffect(
        tool_id=TOOL_ID, effect="read", idempotent=True, sink=SinkDescriptor(egress="external", args_leave_boundary=True)
    )


UNAVAILABLE_EVENT: Final = "x.pipeshub.repository.unavailable"


def unavailable_event(location: RepositoryLocation) -> NewEvent:
    """``x.pipeshub.repository.unavailable``: a clone refused because its host or prefix is not on the org's list."""
    from app.harness.contracts.generated.pipeshub.event_catalog import EVENT_MODELS
    from harness_core.contracts import validate_contract

    data: dict[str, JsonValue] = {"host": location.host, "path": f"/{location.path}", "reason": "host_not_allowed"}
    model = EVENT_MODELS[UNAVAILABLE_EVENT]
    _ = validate_contract(cast("type", model.model_fields["data"].annotation), data)
    return NewEvent(UNAVAILABLE_EVENT, data, critical=False)


REPOSITORY_CACHE_KEY: Final = "/services/agentHarness/repositoryCache"


class StatusStore(Protocol):
    async def set_config(self, key: str, value: dict[str, JsonValue]) -> bool: ...


@dataclass
class CacheStatusPublisher:
    """The deployment's repository cache as the admins' Git hosts screen reads it (``context.deployment.cache``):
    sizes, what it holds and whether its disk is low, written to the configuration store, which Node reads. Written
    when it changes or every ``every_s``, never on each call."""

    fetcher: RepositoryFetcher
    store: StatusStore
    every_s: float = 60.0
    clock: Callable[[], float] = time.monotonic
    _last: tuple[float, dict[str, int | bool]] | None = field(default=None, init=False)

    async def publish(self) -> None:
        status = await asyncio.to_thread(self.fetcher.status)
        now = self.clock()
        if self._last is not None and self._last[1] == status and now - self._last[0] < self.every_s:
            return
        try:
            _ = await self.store.set_config(REPOSITORY_CACHE_KEY, {**status, "updatedAt": int(time.time())})
        except Exception as exc:  # the screen shows the last state; the fetch is not held for it
            _LOG.warning("repository.status_unpublished", errorType=type(exc).__name__)
            return
        self._last = (now, status)


async def publish_cache_status(store: StatusStore, fetcher: RepositoryFetcher | None = None) -> None:
    """The cache's state at worker start (WP84-FU-3): without it an admin learns of an unwritable or full cache only
    once a clone was refused. ``fetcher``: the deployment's unless a test passes one. Never stops the worker (a bad
    cache setting fails its clones, not the start): the screen keeps its last state."""
    try:
        await CacheStatusPublisher(fetcher or deployment_fetcher(), store).publish()
    except Exception as exc:
        _LOG.warning("repository.status_unpublished", errorType=type(exc).__name__)


def fetched_event(fetched_host: str, path: str, size: int, *, cached: bool, full: bool, commit: str) -> NewEvent:
    """``x.pipeshub.repository.fetched``, checked against its contract; non-critical (folds ignore it)."""
    from app.harness.contracts.generated.pipeshub.event_catalog import EVENT_MODELS
    from harness_core.contracts import validate_contract

    data: dict[str, JsonValue] = {
        "host": fetched_host, "repository": path, "bytes": size, "cached": cached, "full": full, "commit": commit,
    }
    model = EVENT_MODELS[FETCHED_EVENT]
    data_model = model.model_fields["data"].annotation
    _ = validate_contract(cast("type", data_model), data)
    return NewEvent(FETCHED_EVENT, data, critical=False)


@dataclass
class RepositoryFetchTools:
    """The host's side of ``repository_fetch``: which hosts, the fetcher, the hand-off, the run's cap and level."""

    fetcher: RepositoryFetcher
    hosts: Callable[[], Awaitable[list[Host]]]
    shelf: HandsOff
    volume: VolumeAccount | None = None
    # What the classification extension point answers for the conversation's egress now: allow, ask or deny.
    egress: Callable[[], str] = lambda: "allow"
    # Whether Node accepts ``x.pipeshub.repository.fetched`` (its claim names ``repository_fetches``).
    journal_events: bool = False
    log: HarnessLog = field(default_factory=lambda: _LOG)
    # Where the cache's state goes for the admins' Git hosts screen (``CacheStatusPublisher``); unset, nowhere.
    status: CacheStatusPublisher | None = None
    # The session's references (ADR-RUN-01 D2): one an earlier turn fetched comes from the cache at the commit it was
    # read at, and each fetch is vouched for, so the checkout is recorded as a reference.
    references: HoldsReferences | None = None
    # The org's answer to "could anyone have read it" (WP83-FU-3): its org-only addresses, read at each call. Unset:
    # the deployment's own ranges only.
    reachability: Callable[[], Awaitable[PublicReachability]] | None = None

    def schemas(self) -> list[ToolSchema]:
        return [
            ToolSchema(
                name=REPOSITORY_FETCH,
                description=(
                    "Clone a public git repository into this sandbox, read-only. Run `ph git clone <https url>` from "
                    "your shell rather than calling this directly: it calls this tool and checks the copy out for you "
                    "(`ph git fetch` refreshes it later). The person is asked first, every time; git hosts are never "
                    "reachable from the shell, and nothing can be pushed this way. Only https URLs on the git hosts "
                    "your administrator allows. By default only the latest commit of the default branch. The copy "
                    "stays in this sandbox and is not saved with the session's files: keep your notes and outputs "
                    "outside it."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "maxLength": 400,
                            "description": "The repository's https URL, like https://github.com/owner/repo",
                        },
                        "ref": {"type": "string", "maxLength": 200, "description": "A branch or tag (default: the default branch)"},
                        "full": {"type": "boolean", "description": "The whole history instead of the latest commit"},
                    },
                    "required": ["url"],
                    "additionalProperties": False,
                },
            )
        ]

    def effect(self, name: str) -> ToolEffect | None:
        return repository_effect() if name == REPOSITORY_FETCH else None

    def provenance(self, name: str) -> ProvenanceRule | None:
        """A fetched repository: written by outsiders, public only by the fetch's own claim (WP-83 D1, D4)."""
        return REPOSITORY_RULE if name == REPOSITORY_FETCH else None

    def size_of(self, org_id: str) -> Callable[[RepositoryLocation, ToolCall], int | None]:
        def size(location: RepositoryLocation, call: ToolCall) -> int | None:
            try:
                ref = check_ref(call.arguments.get("ref"))
            except RepositoryRefused:
                return None
            return self.fetcher.known_size(org_id, location, ref, full=call.arguments.get("full") is True)

        return size

    async def execute(self, call: ToolCall, ctx: ToolContext) -> ToolExecution:
        try:
            return await self._execute(call, ctx)
        except RepositoryRefused as refused:
            self.log.info("repository.refused", reasonCode=refused.reason)
            events: tuple[NewEvent, ...] = ()
            location = repository_of(call)
            if refused.reason == "repository_host_not_allowed" and self.journal_events and location is not None:
                # The chat's "Clone not available" card, which lets an admin turn the host's preset on.
                events = (unavailable_event(location),)
            return ToolExecution("error", refused.message, error_code="failed", events=events)
        finally:
            if self.status is not None:
                await self.status.publish()

    async def _execute(self, call: ToolCall, ctx: ToolContext) -> ToolExecution:
        location = parse_repository_url(call.arguments.get("url"))
        ref = check_ref(call.arguments.get("ref"))
        full = call.arguments.get("full") is True
        if self.egress() == "deny":
            raise RepositoryRefused(
                "repository_classification",
                "This conversation has read data whose classification keeps it inside the organization, so no "
                "repository can be fetched from outside in it.",
            )
        entry = allowed_entry(location, await self.hosts())
        if entry is None:
            raise RepositoryRefused(
                "repository_host_not_allowed",
                f"{location.label} is not on the git hosts your administrator allows for agents. Ask an admin to allow "
                "it (Allowed repositories), or work without it.",
            )
        remaining: int | None = None
        if self.volume is not None:
            allowed, remaining = await self._charge(ctx, 0)
            if not allowed:
                raise RepositoryRefused(
                    "repository_volume_cap", "This run has already moved as much data from outside as it may."
                )
        held = self.references.reference_for(location.shown_url) if self.references is not None else None
        fetched = await self.fetcher.fetch(
            ctx.org_id, location, ref=ref, full=full, max_bytes=remaining, held=held.version if held else None
        )
        if self.volume is not None and not fetched.cached:
            _ = await self._charge(ctx, fetched.size_bytes)
        with self.fetcher.cache.pinned(fetched.key):
            try:
                # Only a complete fetch and a complete tar reach this point: the sandbox's folder gets all of it or nothing.
                folder = await self.shelf.hand_off(file_chunks(fetched.tar), fetched.size_bytes)
            except Exception as exc:  # the folder is full or the sandbox went: nothing reaches the engine
                self.log.warning("repository.hand_off_failed", errorType=type(exc).__name__)
                raise RepositoryRefused(
                    "repository_hand_off_failed",
                    f"The repository ({max(1, fetched.size_bytes // MIB)} MB) does not fit in this sandbox's repository "
                    f"folder ({self.fetcher.config.repo_max_bytes // MIB} MB), with what was already handed over in "
                    "this turn. Ask for the latest commit only, or a smaller repository.",
                ) from None
        if folder is None:
            raise RepositoryRefused(
                "repository_no_sandbox", "The repository was fetched, but this turn has no sandbox to hand it to."
            )
        answer: dict[str, JsonValue] = {
            "path": f"{folder}/{fetched.hand_off_name}",
            "url": location.shown_url,
            "label": location.label,
            "bytes": fetched.size_bytes,
            "commit": fetched.head,
            "full": full,
            "cached": fetched.cached,
            "readOnly": True,
        }
        if ref is not None:
            answer["ref"] = ref
        if self.references is not None:
            self.references.fetched(location.shown_url)
        self.log.info(
            "repository.handed_off", host=location.host, bytes=fetched.size_bytes, cached=fetched.cached, held=held is not None
        )
        events = (
            (fetched_event(location.host, location.path, fetched.size_bytes, cached=fetched.cached, full=full,
                           commit=fetched.head),)
            if self.journal_events
            else ()
        )
        # Outsiders write a repository's content (``external``). It is public only when git dialled a public address
        # (WP-83), the host is one anyone can read and the org does not declare it org-only (WP83-FU-3). The
        # repository at the fetched commit is the result's source: links into it at that commit are citable.
        public = fetched.public and (await self._reachability()).repository_public(location.host, fetched.address, entry)
        claim = ResultProvenance(
            "public" if public else "unknown",
            "external",
            (RepositoryRef(location.shown_url, fetched.head),) if fetched.head else (),
        )
        return ToolExecution("ok", json.dumps(answer), events=events, integrity="external", provenance=claim)

    async def _reachability(self) -> PublicReachability:
        if self.reachability is None:
            return PublicReachability()
        try:
            return await self.reachability()
        except Exception as exc:  # the org's addresses cannot be read: nothing is cleared as public (fail closed)
            self.log.warning("repository.reachability_unread", errorType=type(exc).__name__)
            return UNREADABLE

    async def _charge(self, ctx: ToolContext, received: int) -> tuple[bool, int | None]:
        assert self.volume is not None
        try:
            return await self.volume.charge(org_id=ctx.org_id, run_id=ctx.run_id, received=received)
        except Exception as exc:  # the cap cannot be checked: the fetch does not happen (fail closed)
            self.log.warning("repository.volume_unavailable", errorType=type(exc).__name__)
            raise RepositoryRefused(
                "repository_unavailable", "Repository fetch is unavailable right now: the run's traffic cannot be counted."
            ) from None


class SessionRepositories:
    """``CitableRepositories`` for an engine run: the repositories the session fetched publicly (its provenance
    ledger) and each one's tree at the fetched commit, from the worker's repository cache (the fetch's own listing).
    A tree the cache no longer holds cannot be read, so nothing in that repository is cited (fail closed)."""

    def __init__(self, ledger: ProvenanceLedger, cache: Callable[[], RepositoryCache]) -> None:
        """``cache``: the worker's repository cache, asked for only when a fetched repository's tree is read."""
        self._ledger = ledger
        self._cache = cache
        self._trees: dict[tuple[str, str], RepositoryTree | None] = {}

    def fetched(self) -> tuple[RepositoryRef, ...]:
        return self._ledger.repositories(public_only=True)

    def tree(self, source: RepositoryRef) -> RepositoryTree | None:
        key = (source.url, source.commit)
        if key not in self._trees:
            try:
                self._trees[key] = self._cache().tree_of(repository_label(source), source.commit)
            except OSError:
                self._trees[key] = None
        return self._trees[key]


class PushesSessionBranch(Protocol):
    async def push_session_branch(self, grant: Callable[[str, str], Awaitable[None]]) -> object | None: ...


class GrantsPushes(Protocol):
    """The egress proxy's one-push grants (``POST /v1/push-grants``)."""

    async def grant_push(
        self, *, sandbox_id: str, placeholder: str, host: str, repository: str, branch: str, credential_ref: str,
        ttl_s: int,
    ) -> None: ...


# How long an approved push's placeholder is good for: the push runs at once.
PUSH_GRANT_TTL_S: Final = 120


def push_effect() -> ToolEffect:
    """A write to the repository's host: never repeated unasked, and the flow rule holds it after a private read."""
    return ToolEffect(
        tool_id=PUSH_TOOL_ID, effect="write", idempotent=False, sink=SinkDescriptor(egress="external", args_leave_boundary=True)
    )


@dataclass
class RepositoryPushTools:
    """``repository_push`` (GIT-FLOW under WP-63's rule): the run's commit to its session branch, after the
    person allowed it. The engine's own git fetches and cannot push; this push presents a placeholder the egress proxy
    holds a one-push grant for (this sandbox, repository and branch, ``PUSH_GRANT_TTL_S``), with the push credential
    sealed for that placeholder alone."""

    checkout: GitCheckout
    shelf: PushesSessionBranch
    grants: GrantsPushes
    log: HarnessLog = field(default_factory=lambda: _LOG)

    async def _grant(self, sandbox_id: str, placeholder: str) -> None:
        credentials = self.checkout.credentials
        sealed = None if credentials is None else await credentials.credential_ref("agent", placeholder)
        if sealed is None:
            raise RepositoryRefused("repository_push_unavailable", "This repository has no push credential.")
        await self.grants.grant_push(
            sandbox_id=sandbox_id, placeholder=placeholder, host=self.checkout.host,
            repository=self.checkout.repository, branch=self.checkout.session_branch, credential_ref=sealed,
            ttl_s=PUSH_GRANT_TTL_S,
        )

    def schemas(self) -> list[ToolSchema]:
        where = f"{self.checkout.host}/{self.checkout.repository}"
        return [
            ToolSchema(
                name=REPOSITORY_PUSH,
                description=(
                    f"Push your committed work in {self.checkout.directory} to this session's branch "
                    f"({self.checkout.session_branch}) of {where}. Run `ph git push` from your shell. The person is "
                    "asked first, every time; plain `git push` has no credential. Only the session branch, never forced."
                ),
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            )
        ]

    def effect(self, name: str) -> ToolEffect | None:
        return push_effect() if name == REPOSITORY_PUSH else None

    def provenance(self, name: str) -> ProvenanceRule | None:
        """The push's receipt (branch, repository, commit) is the harness's own text."""
        return NO_CONTENT_RULE if name == REPOSITORY_PUSH else None

    async def execute(self, call: ToolCall, ctx: ToolContext) -> ToolExecution:
        del call, ctx
        try:
            outcome = await self.shelf.push_session_branch(self._grant)
        except RepositoryRefused as refused:
            return ToolExecution("error", refused.message, error_code="failed")
        except Exception as exc:  # the grant could not be registered: nothing was pushed
            self.log.warning("repository.push_grant_failed", errorType=type(exc).__name__)
            return ToolExecution("error", "The push could not be prepared; nothing was pushed. Try again.", error_code="failed")
        if outcome is None:
            return ToolExecution("error", "This turn has no repository to push.", error_code="failed")
        pushed = cast("Any", outcome)
        if not pushed.pushed:
            self.log.info("repository.push_failed")
            return ToolExecution(
                "error",
                "The push was refused (only fast-forwards of this session's branch are allowed): " + str(pushed.detail),
                error_code="failed",
            )
        answer = {"branch": self.checkout.session_branch, "repository": f"{self.checkout.host}/{self.checkout.repository}",
                  "commit": pushed.commit}
        return ToolExecution("ok", json.dumps(answer))


class _Tools(Protocol):
    def schemas(self) -> list[ToolSchema]: ...

    def effect(self, name: str) -> ToolEffect | None: ...

    def provenance(self, name: str) -> ProvenanceRule | None: ...

    async def execute(self, call: ToolCall, ctx: ToolContext) -> ToolExecution: ...


class RepositoryCatalog:
    """The run's catalog with ``repository_fetch`` (and ``repository_push``) beside its own (``ToolCatalog``)."""

    def __init__(self, inner: ToolCatalog, *tools: _Tools) -> None:
        # ADR-RUN-01 D3: a host tool served without a provenance rule is refused.
        _ = require_rules(
            (schema.name for t in tools for schema in t.schemas()),
            {schema.name: t.provenance(schema.name) for t in tools for schema in t.schemas()},
        )
        self._inner = inner
        self._tools = tools

    def provenance(self) -> dict[str, ProvenanceRule]:
        """The rules of the host tools this catalog adds beside the run's own."""
        return {
            schema.name: rule
            for t in self._tools
            for schema in t.schemas()
            if (rule := t.provenance(schema.name)) is not None
        }

    def _owner(self, name: str) -> _Tools | None:
        return next((t for t in self._tools if t.effect(name) is not None), None)

    def describe_call(self, call: ToolCall) -> ToolEffect | None:
        owner = self._owner(call.name)
        return owner.effect(call.name) if owner is not None else self._inner.describe_call(call)

    def schemas(self) -> list[ToolSchema] | None:
        own = self._inner.schemas()
        taken = {schema.name for schema in own or ()}
        added = [s for tools in self._tools for s in tools.schemas() if s.name not in taken]
        return [*(own or []), *added]

    async def execute(self, call: ToolCall, ctx: ToolContext) -> ToolExecution:
        owner = self._owner(call.name)
        if owner is not None:
            return await owner.execute(call, ctx)
        return await self._inner.execute(call, ctx)


class EgressControl:
    """``VolumeAccount`` and ``GrantsPushes`` over the egress proxy's control plane, at the address configured now,
    with the worker credential."""

    def __init__(self, config_service: object) -> None:
        self._config_service = config_service
        self._client: object | None = None

    def _control(self) -> Any:  # noqa: ANN401 - ConfiguredEgressBindings, imported late (the proxy image lacks it)
        if self._client is None:
            from app.harness.credential_roles import worker_client_auth
            from app.harness.sandbox.egress_config import ConfiguredEgressBindings

            service = cast("Any", self._config_service)
            self._client = ConfiguredEgressBindings(service, worker_client_auth(service))
        return self._client

    async def aclose(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await cast("Any", client).aclose()

    async def charge(self, *, org_id: str, run_id: str, received: int) -> tuple[bool, int | None]:
        return cast(
            "tuple[bool, int | None]",
            await self._control().charge_volume(org_id=org_id, run_id=run_id, received=received),
        )

    async def grant_site(self, *, sandbox_id: str, host: str, run_id: str) -> None:
        await self._control().grant_site(sandbox_id=sandbox_id, host=host, run_id=run_id)

    async def hold_flow(self, *, sandbox_id: str, run_id: str, hold: str) -> None:
        await self._control().hold_flow(sandbox_id=sandbox_id, run_id=run_id, hold=hold)

    async def grant_push(
        self, *, sandbox_id: str, placeholder: str, host: str, repository: str, branch: str, credential_ref: str,
        ttl_s: int,
    ) -> None:
        await self._control().grant_push(
            sandbox_id=sandbox_id, placeholder=placeholder, host=host, repository=repository, branch=branch,
            credential_ref=credential_ref, ttl_s=ttl_s,
        )


__all__ = [
    "KNOWN_FORGES",
    "NamedRepository",
    "named_repositories",
    "named_repositories_line",
    "repository_instructions",
    "PUSH_TOOL_ID",
    "REPOSITORY_PUSH",
    "RepositoryPushTools",
    "push_destination",
    "push_effect",
    "ASK_LAYER",
    "ASK_RULE",
    "FETCHED_EVENT",
    "REPOSITORY_FETCH",
    "TOOL_ID",
    "EgressControl",
    "RepositoryAsks",
    "RepositoryCatalog",
    "RepositoryFetchTools",
    "card_label",
    "deployment_fetcher",
    "default_repository_policy",
    "capped_hosts",
    "hosts_held_by",
    "listed_hosts",
    "fetch_config_from_env",
    "fetched_event",
    "unavailable_event",
    "CacheStatusPublisher",
    "publish_cache_status",
    "REPOSITORY_CACHE_KEY",
    "UNAVAILABLE_EVENT",
    "host_ok",
    "live_repository_hosts",
    "org_reachability",
    "deployment_refused_networks",
    "prefix_within",
    "repository_destination",
    "repository_effect",
    "repository_key",
]
