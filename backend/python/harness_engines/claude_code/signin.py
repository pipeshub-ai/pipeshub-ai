"""A person's own Claude sign-in inside Claude Code (SIGNIN, C-1), as SIGNIN-1 recorded the binary doing it.

``claude auth login`` prints a link to Anthropic's page and waits for the ``<code>#<state>`` line that page shows;
it works over the broker's plain pipes. PipesHub shows the link only when its host is on the signed list, writes
the person's code to the prompt once it has the right shape and the link's own state, and keeps neither.
``claude auth status`` reports the account's email and plan, never a token; ``claude auth logout`` revokes at
Anthropic (best effort: it reports success either way, SP-5) and deletes the credentials. The sandbox's whole
config folder is wiped after it, because ``.claude.json`` holds the account's email too (SP-3).

The launch drops every variable that would outrank or redirect the person's login (Claude Code's precedence puts
``ANTHROPIC_AUTH_TOKEN`` and ``ANTHROPIC_API_KEY`` above it) and PipesHub's model alias: the plan picks Claude's
own models. Nothing else changes (CL-6: no login method is removed or restricted).
"""

from __future__ import annotations

import asyncio
import json
import re
import urllib.parse
from typing import TYPE_CHECKING, cast

from harness_core.engines.binary import run_check
from harness_core.engines.signin import (
    KEEP_IDLE_BOUNDS_S,
    SIGNIN_WAIT_S,
    SignedIn,
    SignInRefused,
    SignInReply,
    plan_allowed,
)
from harness_core.engines.types import EngineLaunch
from harness_core.sandbox.egress.types import EGRESS_PROXY_HOST, EGRESS_PROXY_PORT
from harness_core.sandbox.streams import collect
from harness_core.sandbox.types import SandboxFarewell
from harness_engines.claude_code.launch import BINARY_PATH, CONFIG_DIR, FIXED_ENV, MODEL
from harness_engines.claude_code.mapper import own_login_error

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from contextlib import AbstractAsyncContextManager

    from harness_core.engines.signin import SignInAttempt, SignInHosts
    from harness_core.logs import HarnessLog
    from harness_core.sandbox import SandboxHandle
    from harness_core.sandbox.ports import ProcessHandle

LOGIN = (BINARY_PATH, "auth", "login", "--claudeai")
STATUS = (BINARY_PATH, "auth", "status", "--json")
LOGOUT = (BINARY_PATH, "auth", "logout")
PROMPT = "Paste code here if prompted"
CREDENTIALS = f"{CONFIG_DIR}/.credentials.json"
# Claude Code's own settings file: after a sign-in it names the account (email, org, plan; SP-3).
ACCOUNT_FILE = f"{CONFIG_DIR}/.claude.json"
PROCESSES = ("sh", "-c", 'for d in /proc/[0-9]*; do echo "${d#/proc/}"; done')
# Ends every process but those in $SPARE and itself: nothing a turn started in the background, prompt-injected or
# not, runs on into the conversation's next turn or the idle time. The sandbox's own keep-alive is in $SPARE.
PARK = (
    "sh",
    "-c",
    'for d in /proc/[0-9]*; do p="${d#/proc/}"; case " $SPARE $$ " in *" $p "*) ;; *) kill -9 "$p" 2>/dev/null;; '
    "esac; done; exit 0",
)
# Anything here outranks the person's login, sends it elsewhere, or would let PipesHub read its traffic.
OWN_LOGIN_FORBIDDEN_ENV = frozenset(
    {
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "CLAUDE_CODE_OAUTH_REFRESH_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR",
        "CLAUDE_CODE_OAUTH_CLIENT_ID",
        "CLAUDE_CODE_CUSTOM_OAUTH_URL",
        "USE_LOCAL_OAUTH",
        "USE_STAGING_OAUTH",
        "NODE_EXTRA_CA_CERTS",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_FOUNDRY",
    }
)
# PipesHub's neutral model id is the gateway's alias; on a plan, Claude Code's own models apply.
MODEL_ENV = frozenset(
    {
        "ANTHROPIC_MODEL",
        "ANTHROPIC_SMALL_FAST_MODEL",
        "CLAUDE_CODE_SUBAGENT_MODEL",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "ANTHROPIC_DEFAULT_FABLE_MODEL",
    }
)
# HTTPS only: the sandbox's tunnels go through the egress proxy (P5.4b); the tool gateway stays plain HTTP.
PROXY_ENV = {
    "HTTPS_PROXY": f"http://{EGRESS_PROXY_HOST}:{EGRESS_PROXY_PORT}",
    "https_proxy": f"http://{EGRESS_PROXY_HOST}:{EGRESS_PROXY_PORT}",
}
# The folder is root's and sticky (SEC1): the sandbox user removes exactly what it wrote there. Last of all: every
# `claude` command (logout and status too) writes `.claude.json` with the account in it.
WIPE = (
    f'find {CONFIG_DIR} -mindepth 1 -maxdepth 1 -user "$(id -u)" -exec rm -rf {{}} + ; '
    f"test ! -e {CREDENTIALS} && test ! -e {ACCOUNT_FILE}"
)
# What the sandbox broker runs in a kept sandbox it removes itself (GAPS-2: idle or lifetime expiry, a crash-loop
# reap, an orphan no worker took back): the logout that revokes the login at Anthropic, then the wipe, so revocation
# never depends on the worker that parked it. Nothing to revoke without the credentials file. The own-login launch's
# environment but the turn's tool route and skills, which a parked sandbox has no turn for.
FAREWELL = SandboxFarewell(
    argv=(
        "sh",
        "-c",
        f"test -e {CREDENTIALS} || exit {SandboxFarewell.NOTHING_TO_DO}; "
        f'{" ".join(LOGOUT)}; code=$?; {WIPE} || code=1; exit "$code"',
    ),
    env={"CLAUDE_CONFIG_DIR": CONFIG_DIR, "HOME": "/tmp", **FIXED_ENV, **PROXY_ENV},
    # No parked record outlives the longest idle setting and its 2 min grace: a sandbox nobody took back by then
    # (its broker restarted and forgot its keep-warm time) has no turn coming.
    orphan_after_s=KEEP_IDLE_BOUNDS_S[1] + 2 * 60,
)
_LINK = re.compile(r"visit: (https://\S+)")
_CODE_PART = r"[A-Za-z0-9._~-]{1,512}"
_CODE = re.compile(rf"^({_CODE_PART})#({_CODE_PART})$")
LINK_PATHS = frozenset({"/cai/oauth/authorize", "/oauth/authorize"})
LOGIN_TRIES = 3
PRINT_TIMEOUT_S = 30.0
EXCHANGE_TIMEOUT_S = 60.0
_OUTPUT_LIMIT = 64 * 1024


def own_login_launch(launch: EngineLaunch, model: str | None = None) -> EngineLaunch:
    """The gateway launch with every model credential, endpoint and alias removed and the proxy for tunnels. With
    ``model`` (the configured Claude model Node pinned, ADR-MA-01), every model Claude Code would pick is that one, so
    the org's model groups hold on a person's own plan as on every other path."""
    env = {
        k: v
        for k, v in launch.env.items()
        if k not in OWN_LOGIN_FORBIDDEN_ENV and k not in MODEL_ENV
    }
    argv = list(launch.argv)
    if "--model" in argv:
        at = argv.index("--model")
        if argv[at + 1] == MODEL:
            del argv[at : at + 2]
    if model is not None:
        argv += ["--model", model]
        env.update(dict.fromkeys(MODEL_ENV, model))
    own = EngineLaunch(
        engine=launch.engine,
        argv=tuple(argv),
        env={**env, **PROXY_ENV},
        files=launch.files,
        workdir=launch.workdir,
        port=launch.port,
    )
    check_own_login(own)
    return own


def check_own_login(launch: EngineLaunch) -> None:
    """CL-10: an own-login launch holding any gateway credential or redirect is refused, never started."""
    found = sorted(OWN_LOGIN_FORBIDDEN_ENV & set(launch.env))
    if found or MODEL in launch.argv:
        raise ValueError(
            f"an own-login launch must not set {', '.join(found) or 'the gateway model'}"
        )


def is_own_login(launch: EngineLaunch) -> bool:
    """A gateway launch always carries the gateway credential (CL-1); an own-login launch never does."""
    return "ANTHROPIC_AUTH_TOKEN" not in launch.env


def printed_link(output: str) -> str | None:
    found = _LINK.search(output)
    return found.group(1) if found is not None else None


def checked_link(link: str, hosts: SignInHosts) -> str:
    """The link, if it leads to Anthropic's own authorize page on a signed host over https; else refused.

    A compromised engine could print any "sign-in" link: the person is shown none unless it passes."""
    try:
        parts = urllib.parse.urlsplit(link)
        port = parts.port
    except ValueError:
        raise SignInRefused(
            "the sign-in link is malformed", reason="signin_url_refused"
        ) from None
    query = urllib.parse.parse_qs(parts.query)
    redirect = urllib.parse.urlsplit(query.get("redirect_uri", [""])[0])
    ok = (
        parts.scheme == "https"
        and parts.hostname in hosts.link
        and port is None
        and not parts.username
        and not parts.password
        and parts.path in LINK_PATHS
        and query.get("response_type") == ["code"]
        and query.get("code_challenge_method") == ["S256"]
        and len(query.get("state", [""])[0]) > 0
        and redirect.scheme == "https"
        and redirect.hostname in (hosts.link | set(hosts.sandbox))
    )
    if not ok:
        raise SignInRefused(
            "the sign-in link is not Anthropic's", reason="signin_url_refused"
        )
    return link


def code_line(code: str, link: str) -> bytes:
    """The line written to the login's prompt: ``<code>#<state>`` whose state is this login's own.

    Without its state Claude Code refuses the code and waits on (SP-2), and another login's code is no use here."""
    found = _CODE.match(code.strip())
    state = urllib.parse.parse_qs(urllib.parse.urlsplit(link).query).get("state", [""])[
        0
    ]
    if found is None or found.group(2) != state:
        raise SignInRefused(
            "the pasted code is not this sign-in's", reason="signin_code_invalid"
        )
    return f"{found.group(1)}#{found.group(2)}\n".encode()


def signed_in(status: str) -> SignedIn | None:
    """The account ``claude auth status`` reports, when signed in with a Claude account."""
    try:
        doc: object = json.loads(status)
    except ValueError:
        return None
    if not isinstance(doc, dict):
        return None
    fields = cast("dict[str, object]", doc)
    if fields.get("loggedIn") is not True or fields.get("authMethod") != "claude.ai":
        return None
    email, plan = fields.get("email"), fields.get("subscriptionType")
    if not isinstance(email, str) or not isinstance(plan, str) or not email or not plan:
        return None
    return SignedIn(email=email, plan=plan)


async def sign_in(
    sandbox: SandboxHandle,
    launch: EngineLaunch,
    hosts: SignInHosts,
    prompt: Callable[[], AbstractAsyncContextManager[SignInAttempt]],
    progress: Callable[[str, str | None, str | None], Awaitable[None]],
    log: HarnessLog,
) -> SignedIn:
    env = dict(launch.env)
    async with prompt() as attempt:
        for tries_left in range(LOGIN_TRIES - 1, -1, -1):
            process = await sandbox.exec(list(LOGIN), env=env, stdin=True)
            # Both streams are read to the end while the login runs: an undrained pipe would block it.
            stderr = asyncio.ensure_future(collect(process.stderr, limit=_OUTPUT_LIMIT))
            stdout: asyncio.Future[bytes] | None = None
            try:
                found, stdout = await _link(process)
                link = checked_link(found, hosts)
                attempt.show(link)
                await progress("requested", None, None)
                while True:
                    request = await attempt.code(SIGNIN_WAIT_S)
                    try:
                        line = code_line(request.code, link)
                    except SignInRefused as refused:
                        await attempt.reply(
                            SignInReply("failed", refused.reason_code, retry=True)
                        )
                        continue
                    break
                await process.write(line)
                exit_code = await process.wait(timeout_s=EXCHANGE_TIMEOUT_S)
            except BaseException:
                await _kill(process)
                raise
            finally:
                for task in (stderr, stdout):
                    if task is not None:
                        _ = task.cancel()
            if exit_code != 0:
                log.warning(
                    "engine.signin.failed",
                    reasonCode="signin_failed",
                    retry=tries_left > 0,
                )
                await attempt.reply(
                    SignInReply("failed", "signin_failed", retry=tries_left > 0)
                )
                if tries_left > 0:
                    continue
                await progress("failed", "signin_failed", None)
                raise SignInRefused(
                    "Anthropic did not accept the code", reason="signin_failed"
                )
            _, printed = await run_check(sandbox, STATUS, env)
            account = signed_in(printed)
            if account is None:
                await attempt.reply(SignInReply("failed", "signin_failed"))
                await progress("failed", "signin_failed", None)
                raise SignInRefused(
                    "Claude Code reports no Claude account after sign-in",
                    reason="signin_failed",
                )
            if not plan_allowed(account.plan, work_plans_only=request.work_plans_only):
                await sign_out(sandbox, launch, log)
                await attempt.reply(SignInReply("failed", "plan_not_allowed"))
                await progress("failed", "plan_not_allowed", account.plan)
                raise SignInRefused(
                    "This plan is not allowed by the organization",
                    reason="plan_not_allowed",
                )
            await attempt.reply(SignInReply("signed_in", signed_in=account))
            await progress("signed_in", None, account.plan)
            log.info("engine.signin.signed_in", plan=account.plan)
            return account
    raise SignInRefused("sign-in did not finish", reason="signin_failed")


async def sign_out(
    sandbox: SandboxHandle, launch: EngineLaunch, log: HarnessLog
) -> None:
    """Logout (revokes at Anthropic, best effort) and the config folder's own entries wiped; never raises."""
    env = dict(launch.env)
    try:
        code, _ = await run_check(sandbox, LOGOUT, env)
        if code != 0:
            log.warning("engine.signin.logout_failed", exitCode=code)
    except Exception as exc:
        log.warning("engine.signin.logout_failed", errorType=type(exc).__name__)
    try:
        code, _ = await run_check(sandbox, ["sh", "-c", WIPE], {"HOME": "/tmp"})
        if code != 0:
            log.error("engine.signin.wipe_failed", exitCode=code)
    except Exception as exc:
        log.error("engine.signin.wipe_failed", errorType=type(exc).__name__)


async def processes(sandbox: SandboxHandle) -> frozenset[int]:
    """The processes running in the sandbox now (taken before a turn starts any)."""
    _, printed = await run_check(sandbox, PROCESSES, {"HOME": "/tmp"})
    return frozenset(int(line) for line in printed.split() if line.isdigit())


async def park(sandbox: SandboxHandle, spare: frozenset[int], log: HarnessLog) -> bool:
    """Ends every process a turn left (all but ``spare``) before the sandbox is kept; False if that failed."""
    try:
        code, _ = await run_check(sandbox, PARK, {"HOME": "/tmp", "SPARE": " ".join(str(p) for p in sorted(spare))})
    except Exception as exc:
        log.warning("engine.signin.park_failed", errorType=type(exc).__name__)
        return False
    return code == 0


async def _link(process: ProcessHandle) -> tuple[str, asyncio.Future[bytes]]:
    """The link the login prints before its prompt, and the reader that drains the rest of its output."""
    stream = aiter(process.stdout)
    seen = bytearray()

    async def read() -> str:
        async for chunk in stream:
            seen.extend(chunk[: max(0, _OUTPUT_LIMIT - len(seen))])
            text = seen.decode("utf-8", "replace")
            if PROMPT in text:
                link = printed_link(text)
                if link is not None:
                    return link
        raise SignInRefused(
            "Claude Code printed no sign-in link", reason="signin_failed"
        )

    try:
        link = await asyncio.wait_for(read(), timeout=PRINT_TIMEOUT_S)
    except TimeoutError:
        raise SignInRefused(
            "Claude Code printed no sign-in link", reason="signin_failed"
        ) from None
    return link, asyncio.ensure_future(collect(stream, limit=_OUTPUT_LIMIT))


async def _kill(process: ProcessHandle) -> None:
    try:
        await process.signal(9)
        _ = await process.wait(timeout_s=5)
    except Exception:
        return


__all__ = [
    "ACCOUNT_FILE",
    "CREDENTIALS",
    "FAREWELL",
    "LOGIN",
    "LOGOUT",
    "MODEL_ENV",
    "OWN_LOGIN_FORBIDDEN_ENV",
    "PARK",
    "PROCESSES",
    "PROMPT",
    "PROXY_ENV",
    "STATUS",
    "WIPE",
    "check_own_login",
    "checked_link",
    "code_line",
    "is_own_login",
    "own_login_error",
    "own_login_launch",
    "park",
    "printed_link",
    "processes",
    "sign_in",
    "sign_out",
    "signed_in",
]
