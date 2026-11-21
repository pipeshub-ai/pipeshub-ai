"""A run's repository (GIT-FLOW): cloned in a setup phase, worked on in the agent phase, pushed to its session branch.

The two phases are two egress policies on the same sandbox (SEC-EG-11, C6.10), switched before the engine starts:

- **setup**: the git host as a fetch-only destination of the one repository, with a read credential the egress
  proxy injects. ``git clone`` runs with hooks off, no submodules, no templates and no user or system config.
- **agent**: the same host, fetch only (``GitAccess`` without a push branch), with the read credential the proxy
  injects. The setup route, and its placeholder, are gone. A push happens only when a person allowed it
  (``repository_push``): the worker registers a one-push grant at the proxy for a fresh placeholder
  (``egress.push_grants``), and ``push_session_branch`` pushes the session branch's fast-forward with it.

No token ever enters the sandbox (OWD-1): it sends a per-attempt placeholder as a Basic password, which the proxy
swaps; the placeholder is passed in each command's environment and never written to the repository's config.
Submodules are never fetched: each one ``.gitmodules`` names is reported against the agent phase's egress policy.
"""

from __future__ import annotations

import asyncio
import base64
import configparser
import re
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Final, Literal, Protocol
from urllib.parse import urlsplit

from harness_core.engines.errors import EngineRefusal
from harness_core.sandbox.egress.git import branch_ok, repository_ok
from harness_core.sandbox.egress.policy import normalize_host
from harness_core.sandbox.egress.trust import (
    PROXY_URL,
    TRUST_BUNDLE,
    proxy_env,
    trust_env,
    trusted_command,
)
from harness_core.sandbox.types import (
    CredentialInjection,
    EgressDestination,
    EgressPolicy,
    GitAccess,
    TokenRoute,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

    from harness_core.sandbox.egress.sealed import CredentialSealer
    from harness_core.sandbox.ports import SandboxHandle

GitPhaseName = Literal["setup", "agent"]

SETUP_ROUTE: Final = "git:setup"
PUSH_ROUTE: Final = "git:push"
SESSION_BRANCH_PREFIX: Final = "pipeshub/session-"
# The Basic user name the sandbox sends with its placeholder; the proxy replaces the whole credential.
PLACEHOLDER_USER: Final = "pxh"
_SAFE_DIR = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,99}")
_SESSION_ID = re.compile(r"[^A-Za-z0-9._-]+")
# Clone and setup; the work is bounded by the sandbox's own limits, this only by a hung host.
SETUP_TIMEOUT_S: Final = 600.0
# Config a cloned repository can never bring along, and that no user or system config may add back.
_HARDENED: Final = (
    ("core.hooksPath", "/dev/null"),
    ("core.fsmonitor", "false"),
    ("submodule.recurse", "false"),
    ("protocol.allow", "never"),
    ("protocol.https.allow", "always"),
    ("protocol.http.allow", "always"),
    ("credential.helper", ""),
)
_AUTH_ENV: Final = "PXH_GIT_AUTH"

# The clone: every argument positional (never interpolated), the placeholder only in this process's environment.
CLONE_SCRIPT: Final = r"""set -eu
dir="$1"; url="$2"; branch="$3"; base="$4"
if [ -e "$dir/.git" ]; then echo "pxh-git: $dir already holds a repository" >&2; exit 3; fi
set -- clone --no-recurse-submodules --template= --no-tags
if [ -n "$base" ]; then set -- "$@" --branch "$base"; fi
git -c "http.extraHeader=Authorization: Basic $PXH_GIT_AUTH" "$@" -- "$url" "$dir"
cd "$dir"
git config core.hooksPath /dev/null
git config core.fsmonitor false
git config submodule.recurse false
git checkout -q -B "$branch"
git rev-parse HEAD
"""

# What the agent phase may not find in the clone's config: a credential, a header, the setup placeholder.
VERIFY_SCRIPT: Final = r"""set -eu
dir="$1"; needle="$2"
listed=$(git -C "$dir" config --local --list)
case "$listed" in *extraheader*|*credential.*|*"$needle"*) echo "pxh-git: credentials left in config" >&2; exit 4;; esac
if grep -rqsF -- "$needle" "$dir/.git"; then echo "pxh-git: setup placeholder left in the repository" >&2; exit 4; fi
"""


class GitCredentials(Protocol):
    """The repository's credentials as the egress proxy opens them: ``user:token`` sealed for one placeholder."""

    async def credential_ref(self, phase: GitPhaseName, placeholder: str) -> str | None:
        """``setup``: a read credential; ``agent``: one that may push. ``None``: none for this phase."""
        ...


class SealedGitCredentials:
    """Credentials a trusted caller holds, sealed for each phase's placeholder (``user:token``)."""

    def __init__(self, sealer: CredentialSealer, *, read: str | None, push: str | None) -> None:
        self._sealer = sealer
        self._by_phase: dict[str, str | None] = {"setup": read, "agent": push}

    async def credential_ref(self, phase: GitPhaseName, placeholder: str) -> str | None:
        credential = self._by_phase[phase]
        return None if credential is None else self._sealer.seal(placeholder, credential)


class GitSetupFailed(EngineRefusal):
    """The setup phase could not clone, or left something behind; the run does not start its engine."""

    reason_code = "git_setup_failed"


@dataclass(frozen=True, slots=True)
class GitCheckout:
    """The repository a run works on, as its caller resolved it.

    ``url``: the repository's https (or, behind a trusted upstream, http) URL, without credentials.
    ``credentials``: seals each phase's credential for its placeholder; unset, a public repository read without
    one (and never pushed). ``upstream``: where the proxy forwards the host (tests, an enterprise mirror); unset,
    the host's public address. ``directory``: the clone's folder in the workspace.
    """

    url: str
    session_branch: str
    directory: str = "repo"
    base_ref: str | None = None
    credentials: GitCredentials | None = field(default=None, compare=False)
    upstream: str | None = None
    # False: the agent phase may only fetch, as the setup phase (a dry run, G-03: nothing leaves the run).
    push: bool = True

    def __post_init__(self) -> None:
        parts = urlsplit(self.url)
        if parts.scheme not in ("https", "http") or not parts.hostname or parts.username or parts.password:
            raise ValueError("a repository URL is a plain http(s) URL without credentials")
        if parts.query or parts.fragment or not repository_ok(self.repository):
            raise ValueError("a repository URL names one repository")
        if not branch_ok(self.session_branch) or (self.base_ref is not None and not branch_ok(self.base_ref)):
            raise ValueError("a branch name is malformed")
        if not _SAFE_DIR.fullmatch(self.directory) or self.directory in (".", ".."):
            raise ValueError("the clone's directory is one plain folder name")

    @property
    def host(self) -> str:
        return normalize_host(urlsplit(self.url).hostname or "")

    @property
    def port(self) -> int:
        parts = urlsplit(self.url)
        return parts.port or (443 if parts.scheme == "https" else 80)

    @property
    def repository(self) -> str:
        return urlsplit(self.url).path.strip("/").removesuffix(".git")

    @property
    def tls(self) -> bool:
        return urlsplit(self.url).scheme == "https"


def session_branch(session_id: str) -> str:
    """The one branch a session may push: its own, named after it."""
    return SESSION_BRANCH_PREFIX + (_SESSION_ID.sub("-", session_id).strip("-.") or "session")[:80]


def _destination(checkout: GitCheckout, access: GitAccess) -> EgressDestination:
    return EgressDestination(checkout.host, (checkout.port,), upstream=checkout.upstream, git=access)


async def phase_credential(checkout: GitCheckout, phase: GitPhaseName, placeholder: str) -> str | None:
    return None if checkout.credentials is None else await checkout.credentials.credential_ref(phase, placeholder)


def _route(placeholder: str, checkout: GitCheckout, credential: str | None) -> tuple[TokenRoute, ...]:
    if credential is None:
        return ()
    return (TokenRoute(placeholder, checkout.host, credential, CredentialInjection.BASIC),)


def setup_policy(base: EgressPolicy, checkout: GitCheckout, placeholder: str, credential: str | None) -> EgressPolicy:
    """The setup phase: ``base`` and the repository, fetch only, with the read credential."""
    return replace(
        base,
        destinations=(*base.destinations, _destination(checkout, GitAccess(checkout.repository))),
        token_routes=(*base.token_routes, *_route(placeholder, checkout, credential)),
    )


def agent_policy(base: EgressPolicy, checkout: GitCheckout, placeholder: str, credential: str | None) -> EgressPolicy:
    """The agent phase: ``base`` and the repository, fetch only, with the read credential. The proxy refuses every
    push but one a person allowed, which presents a one-push grant's placeholder (WP-63 rule)."""
    access = GitAccess(checkout.repository)
    return replace(
        base,
        destinations=(*base.destinations, _destination(checkout, access)),
        token_routes=(*base.token_routes, *_route(placeholder, checkout, credential)),
    )


def basic_auth(placeholder: str) -> str:
    return base64.b64encode(f"{PLACEHOLDER_USER}:{placeholder}".encode()).decode("ascii")


def _git_config_env(pairs: tuple[tuple[str, str], ...]) -> dict[str, str]:
    env = {"GIT_CONFIG_COUNT": str(len(pairs))}
    for index, (key, value) in enumerate(pairs):
        env[f"GIT_CONFIG_KEY_{index}"] = key
        env[f"GIT_CONFIG_VALUE_{index}"] = value
    return env


def _base_env(checkout: GitCheckout) -> dict[str, str]:
    return {
        **proxy_env(),
        **(trust_env() if checkout.tls else {}),
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_LFS_SKIP_SMUDGE": "1",
    }


def setup_env(checkout: GitCheckout, placeholder: str) -> dict[str, str]:
    """The clone's environment: the proxy, no user or system config, the hardened config, the setup placeholder."""
    return {**_base_env(checkout), **_git_config_env(_HARDENED), _AUTH_ENV: basic_auth(placeholder)}


def agent_git_env(
    checkout: GitCheckout, placeholder: str | None, extra: tuple[tuple[str, str], ...] = ()
) -> dict[str, str]:
    """What the engine's own git reads in the agent phase, as git config in its environment: the proxy (for git only:
    the engine's other traffic keeps its own route), the proxy's authority for an https repository, no hooks or
    submodule recursion, and for a pushable repository the push placeholder (``None``: no push credential) as this
    repository's Basic header. Never in a file the workspace saves (each attempt has its own placeholder)."""
    pairs: tuple[tuple[str, str], ...] = (
        ("http.proxy", PROXY_URL),
        *((("http.sslCAInfo", TRUST_BUNDLE),) if checkout.tls else ()),
        ("core.hooksPath", "/dev/null"),
        ("submodule.recurse", "false"),
    )
    if placeholder is not None:
        prefix = checkout.url.removesuffix("/")
        pairs = (*pairs, (f"http.{prefix}.extraHeader", f"Authorization: Basic {basic_auth(placeholder)}"))
    # ``extra``: the org's git mirrors (WP-100), in the same one set: git reads a single GIT_CONFIG_COUNT.
    return {"GIT_TERMINAL_PROMPT": "0", **_git_config_env((*pairs, *extra))}


def git_config_env(pairs: tuple[tuple[str, str], ...]) -> dict[str, str]:
    """``pairs`` as git configuration in an environment (``GIT_CONFIG_COUNT`` and its keys and values)."""
    return _git_config_env(pairs)


@dataclass(frozen=True, slots=True)
class SubmoduleCheck:
    """A submodule ``.gitmodules`` names, and whether the agent phase's egress could reach it (it is never fetched)."""

    name: str
    url: str
    reachable: bool


def submodule_report(gitmodules: str, policy: EgressPolicy) -> tuple[SubmoduleCheck, ...]:
    """Each submodule URL checked against ``policy``: only an http(s) URL to an allowlisted host and port counts as
    reachable; ``file:``, ``ext::``, ``ssh`` and anything else never is."""
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read_string(gitmodules)
    except configparser.Error:
        return ()
    checks: list[SubmoduleCheck] = []
    for section in parser.sections():
        if not section.startswith("submodule "):
            continue
        url = parser.get(section, "url", fallback="").strip()
        checks.append(SubmoduleCheck(section[len("submodule ") :].strip('"'), url, _reachable(url, policy)))
    return tuple(checks)


def _reachable(url: str, policy: EgressPolicy) -> bool:
    try:
        parts = urlsplit(url)
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        return False
    host = normalize_host(parts.hostname)
    return any(normalize_host(d.host) == host and port in d.ports for d in policy.destinations)


@dataclass(frozen=True, slots=True)
class ClonedRepository:
    head: str
    submodules: tuple[SubmoduleCheck, ...]


async def _run(sandbox: SandboxHandle, argv: list[str], env: Mapping[str, str]) -> tuple[int, str, str]:
    process = await sandbox.exec(argv, env=env)
    out: list[bytes] = []
    err: list[bytes] = []

    async def drain(stream: AsyncIterator[bytes], into: list[bytes]) -> None:
        into.extend([chunk async for chunk in stream])

    _ = await asyncio.gather(drain(process.stdout, out), drain(process.stderr, err))
    code = await process.wait(timeout_s=SETUP_TIMEOUT_S)
    return code, b"".join(out).decode("utf-8", "replace"), b"".join(err).decode("utf-8", "replace")


# A command's exit code and output in a sandbox, for the other setup steps of a turn (WP-63's trust bundle).
run_in = _run


async def clone(sandbox: SandboxHandle, checkout: GitCheckout, *, workspace_root: str, placeholder: str) -> str:
    """The setup phase's clone into ``workspace_root/directory``, on the session branch; the commit it checked out."""
    directory = f"{workspace_root.rstrip('/')}/{checkout.directory}"
    argv = ["sh", "-c", CLONE_SCRIPT, "pxh-git-clone", directory, checkout.url, checkout.session_branch,
            checkout.base_ref or ""]
    code, out, err = await _run(sandbox, trusted_command(argv) if checkout.tls else argv, setup_env(checkout, placeholder))
    if code != 0:
        raise GitSetupFailed(f"git clone failed ({code}): {_tail(err)}", reason="git_clone_failed")
    return out.strip().rsplit("\n", 1)[-1]


async def trust_proxy(sandbox: SandboxHandle, checkout: GitCheckout) -> None:
    """Each agent-phase sandbox of an https repository holds the proxy's authority where the engine's git reads it."""
    if not checkout.tls:
        return
    code, _, err = await _run(sandbox, trusted_command(["sh", "-c", "exit 0"]), trust_env())
    if code != 0:
        raise GitSetupFailed(f"the egress proxy's authority could not be installed: {_tail(err)}", reason="git_trust_failed")


async def verify_agent_phase(
    sandbox: SandboxHandle, checkout: GitCheckout, *, workspace_root: str, setup_placeholder: str
) -> None:
    """Before the engine starts: nothing of the setup phase's credential is left in the repository."""
    directory = f"{workspace_root.rstrip('/')}/{checkout.directory}"
    argv = ["sh", "-c", VERIFY_SCRIPT, "pxh-git-verify", directory, setup_placeholder]
    env = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}
    code, _, err = await _run(sandbox, argv, env)
    if code != 0:
        raise GitSetupFailed(f"the setup phase left credentials behind: {_tail(err)}", reason="git_setup_residue")


async def gitmodules(sandbox: SandboxHandle, checkout: GitCheckout, *, workspace_root: str) -> str:
    path = f"{workspace_root.rstrip('/')}/{checkout.directory}/.gitmodules"
    code, out, _ = await _run(sandbox, ["sh", "-c", 'cat -- "$1" 2>/dev/null || true', "pxh-git", path], {})
    return out if code == 0 else ""


def _tail(text: str, limit: int = 400) -> str:
    return text.strip()[-limit:]


# The one push a person allowed (WP-63 rule: a person confirms every git command that leaves the sandbox): the
# current commit to the session branch, never forced, with a placeholder the proxy accepts for this push only.
PUSH_SCRIPT: Final = r"""set -eu
dir="$1"; branch="$2"
cd "$dir"
git push --porcelain origin "HEAD:refs/heads/$branch"
git rev-parse HEAD
"""


@dataclass(frozen=True, slots=True)
class PushOutcome:
    pushed: bool
    commit: str | None
    detail: str


async def push_session_branch(
    sandbox: SandboxHandle, checkout: GitCheckout, *, workspace_root: str, placeholder: str
) -> PushOutcome:
    """Pushes the clone's current commit to the session branch from inside the sandbox, after a person allowed it.
    ``placeholder`` is the one the proxy holds a one-push grant for: spent by this push, useless after it; the proxy
    still holds the push to that branch's fast-forwards."""
    directory = f"{workspace_root.rstrip('/')}/{checkout.directory}"
    argv = ["sh", "-c", PUSH_SCRIPT, "pxh-git-push", directory, checkout.session_branch]
    env = {**agent_git_env(checkout, placeholder), "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}
    code, out, err = await _run(sandbox, trusted_command(argv) if checkout.tls else argv, env)
    if code != 0:
        return PushOutcome(False, None, _tail(err, 300).replace(placeholder, "***"))
    return PushOutcome(True, out.strip().rsplit("\n", 1)[-1], "")


__all__ = [
    "PUSH_SCRIPT",
    "PushOutcome",
    "push_session_branch",
    "run_in",
    "PUSH_ROUTE",
    "SESSION_BRANCH_PREFIX",
    "SETUP_ROUTE",
    "ClonedRepository",
    "GitCheckout",
    "GitCredentials",
    "GitPhaseName",
    "GitSetupFailed",
    "SealedGitCredentials",
    "SubmoduleCheck",
    "agent_git_env",
    "agent_policy",
    "basic_auth",
    "clone",
    "gitmodules",
    "phase_credential",
    "session_branch",
    "setup_env",
    "setup_policy",
    "submodule_report",
    "trust_proxy",
    "verify_agent_phase",
]
