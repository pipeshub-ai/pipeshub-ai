"""SIGNIN-2 on Claude Code: the own-login launch (CL-9, CL-10), the link and code checks (SA-9, SA-10), status and
error mapping (EM-1..EM-4) on SIGNIN-1's recording, and the login driver over a scripted sandbox (SD-1..SD-7)."""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest

from harness_core.access.types import AccessRouteRef, ModelApi
from harness_core.engines.events import EngineError, UsageReported
from harness_core.engines.signin import (
    CodeRequest,
    SignInHosts,
    SignInRefused,
    SignInReply,
)
from harness_core.engines.types import (
    EngineMode,
    EngineProfile,
    EngineVersion,
    GatewayEndpoints,
)
from harness_core.logs import HarnessLog
from harness_engines.claude_code.launch import MODEL, render_launch
from harness_engines.claude_code.mapper import ClaudeEventMapper
from harness_engines.claude_code.signin import (
    ACCOUNT_FILE,
    CREDENTIALS,
    LOGIN,
    LOGOUT,
    OWN_LOGIN_FORBIDDEN_ENV,
    PARK,
    PROCESSES,
    PROXY_ENV,
    STATUS,
    check_own_login,
    checked_link,
    code_line,
    is_own_login,
    own_login_error,
    own_login_launch,
    park,
    printed_link,
    processes,
    sign_in,
    sign_out,
    signed_in,
)
from tests.unit.harness_engines.claude_code.signin_lab.lab import FIXTURES

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping, Sequence

CLAUDE = EngineVersion("claude_code", "2.1.294", "sha256:" + "a" * 64)
ENDPOINTS = GatewayEndpoints(
    model_base_url="http://model-gateway:8097/v1",
    model_api=ModelApi.ANTHROPIC_MESSAGES,
    tools_mcp_url="http://tool-gateway:8097/mcp",
    token_placeholder="pxh-model-placeholder-0123",
    tools_token_placeholder="pxh-tools-placeholder-4567",
)
HOSTS = SignInHosts(sandbox=("api.anthropic.com", "platform.claude.com"), link=frozenset({"claude.com", "claude.ai"}))
RECORDED: dict[str, Any] = json.loads((FIXTURES / "recorded.json").read_text())
LINK: str = RECORDED["login"]["url"]
STATE = LINK.rsplit("state=", 1)[1]
RECORDED_EMAIL = "ada@example.com"


def _launch() -> Any:  # noqa: ANN401
    profile = EngineProfile(engine=CLAUDE, mode=EngineMode.BUILD, access=AccessRouteRef("route-1", "d1"))
    return render_launch(profile, ENDPOINTS, "/skills")


# CL-9, CL-10: the own-login launch


def test_r38_an_own_login_runs_the_pinned_claude_model_for_every_model_claude_code_picks() -> None:
    """ADR-MA-01 (R38-AUTHZ): the configured model Node resolved within the person's groups, never the gateway alias."""
    own = own_login_launch(_launch(), "claude-sonnet-4-5")
    at = own.argv.index("--model")
    assert own.argv[at + 1] == "claude-sonnet-4-5" and own.argv.count("--model") == 1
    assert MODEL not in own.argv
    for name in ("ANTHROPIC_MODEL", "ANTHROPIC_SMALL_FAST_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL"):
        assert own.env[name] == "claude-sonnet-4-5"
    assert is_own_login(own)


def test_cl9_own_login_drops_every_gateway_credential_and_alias_and_keeps_the_rest() -> None:
    gateway = _launch()
    own = own_login_launch(gateway)
    assert is_own_login(own) and not is_own_login(gateway)
    for name in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL"):
        assert name in gateway.env or name == "ANTHROPIC_API_KEY"
        assert name not in own.env
    assert ENDPOINTS.token_placeholder not in json.dumps(dict(own.env))
    assert MODEL not in own.argv and "--model" not in own.argv
    assert {k: own.env[k] for k in PROXY_ENV} == PROXY_ENV
    assert "HTTP_PROXY" not in own.env and "http_proxy" not in own.env
    # Everything else is the gateway launch's: the tool gateway, the config folder, every switch (CL-6, CL-8).
    kept = {k: v for k, v in gateway.env.items() if k in own.env}
    assert kept == {k: own.env[k] for k in kept}
    assert own.env["HARNESS_TOOLS_TOKEN"] == ENDPOINTS.tools_placeholder
    assert own.env["CLAUDE_CONFIG_DIR"] == "/state/claude"
    assert [a for a in gateway.argv if a not in ("--model", MODEL)] == list(own.argv)
    assert own.files == gateway.files
    text = "\n".join([*own.argv, *own.env])
    for banned in ("--bare", "forceLoginMethod", "DISABLE_LOGIN_COMMAND", "DISABLE_LOGOUT_COMMAND"):
        assert banned not in text


@pytest.mark.parametrize("name", sorted(OWN_LOGIN_FORBIDDEN_ENV))
def test_cl10_an_own_login_launch_holding_a_credential_or_redirect_is_refused(name: str) -> None:
    own = own_login_launch(_launch())
    with pytest.raises(ValueError, match=name):
        check_own_login(dataclasses.replace(own, env={**own.env, name: "x"}))


def test_cl10_the_gateway_model_is_refused_too() -> None:
    own = own_login_launch(_launch())
    with pytest.raises(ValueError, match="gateway model"):
        check_own_login(dataclasses.replace(own, argv=(*own.argv, "--model", MODEL)))


# SA-9, SA-10: the link and the code


def test_sa9_the_recorded_link_is_found_and_passes() -> None:
    assert printed_link(RECORDED["login"]["stdout"]) == LINK
    assert checked_link(LINK, HOSTS) == LINK


@pytest.mark.parametrize(
    "link",
    [
        LINK.replace("https://", "http://"),
        LINK.replace("claude.com", "claude.com.evil.example"),
        LINK.replace("claude.com", "evil.example"),
        LINK.replace("claude.com", "claude.com:8443"),
        LINK.replace("claude.com", "user@claude.com"),
        LINK.replace("/cai/oauth/authorize", "/login"),
        LINK.replace("code_challenge_method=S256", "code_challenge_method=plain"),
        LINK.replace("response_type=code", "response_type=token"),
        LINK.replace("redirect_uri=https%3A%2F%2Fplatform.claude.com", "redirect_uri=https%3A%2F%2Fevil.example"),
        LINK.replace(f"state={STATE}", "state="),
        "https://[::1]/cai/oauth/authorize",
        "not a link",
    ],
)
def test_sa9_a_link_off_anthropics_signed_hosts_is_never_shown(link: str) -> None:
    with pytest.raises(SignInRefused) as refused:
        _ = checked_link(link, HOSTS)
    assert refused.value.reason_code == "signin_url_refused"


def test_sa10_the_code_line_carries_this_logins_own_state() -> None:
    assert code_line(f"FAKECODE123#{STATE}", LINK) == f"FAKECODE123#{STATE}\n".encode()
    assert code_line(f"  FAKECODE123#{STATE}\n", LINK) == f"FAKECODE123#{STATE}\n".encode()


@pytest.mark.parametrize(
    "code",
    ["FAKECODE123", f"FAKECODE123#{STATE}x", "FAKECODE123#other", f"a b#{STATE}", f"abc\n#{STATE}",
     f"abc#{STATE}\nrm -rf /", f"abc#{STATE}#x", "#" + STATE, "x" * 600 + "#" + STATE],
)
def test_sa10_a_code_of_another_shape_or_login_is_never_written(code: str) -> None:
    with pytest.raises(SignInRefused) as refused:
        _ = code_line(code, LINK)
    assert refused.value.reason_code == "signin_code_invalid"


# EM-1..EM-4: status and errors, from the recording


def test_em1_status_names_the_account_and_plan_only_when_signed_in() -> None:
    account = signed_in(RECORDED["status_signed_in"]["json"]["stdout"])
    assert account is not None and (account.email, account.plan) == ("ada@example.com", "max")
    assert signed_in(RECORDED["status_signed_out"]["json"]["stdout"]) is None
    assert signed_in(RECORDED["status_after_logout"]["json"]["stdout"]) is None
    assert signed_in("not json") is None
    for plan, status in RECORDED["plans"].items():
        found = signed_in(json.dumps(status))
        assert found is not None and found.plan == plan.removeprefix("claude_")


def _result(step: str) -> dict[str, Any]:
    return next(e for e in RECORDED[step]["events"] if e["type"] == "result")


def test_em2_expired_limit_and_signed_out_get_their_own_codes() -> None:
    assert own_login_error(_result("prompt_expired")) == "account_expired"
    assert own_login_error(_result("prompt_limit")) == "plan_limit_reached"
    assert own_login_error(_result("prompt_signed_out")) == "signin_required"
    assert own_login_error({"api_error_status": 500, "result": "x"}) is None


def test_em3_the_mapper_names_them_only_on_an_own_login() -> None:
    expired = {**_result("prompt_expired"), "usage": {}}
    own = ClaudeEventMapper(turn_id="t", own_login=True)._result(expired)  # pyright: ignore[reportPrivateUsage]
    gateway = ClaudeEventMapper(turn_id="t")._result(expired)  # pyright: ignore[reportPrivateUsage]
    assert [e.code for e in own if isinstance(e, EngineError)] == ["account_expired"]
    assert [e.code for e in gateway if isinstance(e, EngineError)] == ["http_401"]


def test_em4_claude_codes_dollar_figure_is_never_reported_on_a_plan() -> None:
    ok = {"subtype": "success", "is_error": False, "result": "hi", "total_cost_usd": 0.5,
          "usage": {"input_tokens": 3, "output_tokens": 2},
          "modelUsage": {"m": {"costBasis": "list_price"}}}
    own = ClaudeEventMapper(turn_id="t", own_login=True)._result(ok)  # pyright: ignore[reportPrivateUsage]
    gateway = ClaudeEventMapper(turn_id="t")._result(ok)  # pyright: ignore[reportPrivateUsage]
    [own_usage] = [e for e in own if isinstance(e, UsageReported)]
    [gateway_usage] = [e for e in gateway if isinstance(e, UsageReported)]
    assert (own_usage.cost_usd, own_usage.input_tokens) == (None, 3)
    assert gateway_usage.cost_usd == 0.5


# SD-1..SD-7: the login driver over a scripted sandbox


@dataclass
class Process:
    out: bytes
    exit_code: int = 0
    writes: list[bytes] = field(default_factory=list[bytes])
    killed: bool = False
    _written: asyncio.Event = field(default_factory=asyncio.Event)
    hold: bool = False

    async def _stream(self) -> AsyncIterator[bytes]:
        yield self.out
        if self.hold:
            await self._written.wait()

    async def _empty(self) -> AsyncIterator[bytes]:
        for chunk in ():
            yield chunk

    async def write(self, data: bytes) -> None:
        self.writes.append(data)
        self._written.set()

    async def close_stdin(self) -> None:
        return

    async def signal(self, sig: int) -> None:
        self.killed = True
        self._written.set()

    async def wait(self, *, timeout_s: float | None = None) -> int:
        if self.hold and not self.killed:
            await self._written.wait()
        return 137 if self.killed else self.exit_code


class Proc:
    """A ``ProcessHandle`` over ``Process``: stdout and stderr as properties, as the broker's are."""

    def __init__(self, inner: Process) -> None:
        self.inner = inner

    @property
    def stdout(self) -> AsyncIterator[bytes]:
        return self.inner._stream()  # pyright: ignore[reportPrivateUsage]

    @property
    def stderr(self) -> AsyncIterator[bytes]:
        return self.inner._empty()  # pyright: ignore[reportPrivateUsage]

    async def write(self, data: bytes) -> None:
        await self.inner.write(data)

    async def close_stdin(self) -> None:
        return

    async def signal(self, sig: int) -> None:
        await self.inner.signal(sig)

    async def wait(self, *, timeout_s: float | None = None) -> int:
        return await self.inner.wait(timeout_s=timeout_s)


@dataclass
class Sandbox:
    """Answers each command with the recorded output: logins in order, then status and logout."""

    logins: list[Process]
    status: str = RECORDED["status_signed_in"]["json"]["stdout"]
    commands: list[tuple[str, ...]] = field(default_factory=list[tuple[str, ...]])
    envs: list[dict[str, str]] = field(default_factory=list[dict[str, str]])
    id: str = "sbx-1"

    async def exec(self, argv: Sequence[str], *, env: Mapping[str, str], stdin: bool = False) -> Proc:
        self.commands.append(tuple(argv))
        self.envs.append(dict(env))
        if tuple(argv) == LOGIN:
            return Proc(self.logins.pop(0))
        if tuple(argv) == STATUS:
            return Proc(Process(self.status.encode()))
        return Proc(Process(b""))


def login(exit_code: int = 0, *, url: str = LINK) -> Process:
    out = RECORDED["login"]["stdout"].replace(LINK, url).split("Paste code here if prompted > ")[0]
    return Process((out + "Paste code here if prompted > ").encode(), exit_code=exit_code, hold=True)


@dataclass
class Attempt:
    codes: list[CodeRequest | SignInRefused]
    shown: list[str] = field(default_factory=list[str])
    replies: list[SignInReply] = field(default_factory=list[SignInReply])
    attempt_id: str = "sin_1"

    def show(self, link: str) -> None:
        self.shown.append(link)

    async def code(self, timeout_s: float) -> CodeRequest:
        nxt = self.codes.pop(0)
        if isinstance(nxt, SignInRefused):
            raise nxt
        return nxt

    async def reply(self, reply: SignInReply) -> None:
        self.replies.append(reply)


async def _drive(sandbox: Sandbox, attempt: Attempt) -> tuple[Any, list[tuple[str, str | None, str | None]]]:
    shown: list[tuple[str, str | None, str | None]] = []

    async def progress(state: str, reason: str | None, plan: str | None) -> None:
        shown.append((state, reason, plan))

    @contextlib.asynccontextmanager
    async def prompt() -> AsyncIterator[Attempt]:
        yield attempt

    own = own_login_launch(_launch())
    try:
        result: Any = await sign_in(sandbox, own, HOSTS, prompt, progress, HarnessLog.named("test"))  # type: ignore[arg-type]
    except SignInRefused as refused:
        result = refused
    return result, shown


def _code(work_only: bool = True) -> CodeRequest:
    return CodeRequest(f"FAKECODE123#{STATE}", work_plans_only=work_only)


async def test_sd1_the_link_is_shown_the_code_written_and_the_account_reported() -> None:
    sandbox = Sandbox([login()])
    sandbox.status = json.dumps({**json.loads(sandbox.status), "subscriptionType": "team"})
    process = sandbox.logins[0]
    attempt = Attempt([_code()])
    result, shown = await _drive(sandbox, attempt)
    assert (result.email, result.plan) == ("ada@example.com", "team")
    assert attempt.shown == [LINK]
    assert process.writes == [f"FAKECODE123#{STATE}\n".encode()]
    assert [r.state for r in attempt.replies] == ["signed_in"]
    assert shown == [("requested", None, None), ("signed_in", None, "team")]
    assert sandbox.commands == [LOGIN, STATUS]
    # Every command runs without a gateway credential, through the proxy.
    assert all("ANTHROPIC_AUTH_TOKEN" not in env and env["HTTPS_PROXY"] for env in sandbox.envs)


async def test_sd2_a_consumer_plan_is_signed_out_when_the_org_admits_work_plans_only() -> None:
    sandbox = Sandbox([login()])
    attempt = Attempt([_code(work_only=True)])
    result, shown = await _drive(sandbox, attempt)
    assert isinstance(result, SignInRefused) and result.reason_code == "plan_not_allowed"
    assert LOGOUT in sandbox.commands
    assert any(c[0] == "sh" and "rm -rf" in c[2] for c in sandbox.commands)
    assert [(r.state, r.reason_code) for r in attempt.replies] == [("failed", "plan_not_allowed")]
    assert shown[-1] == ("failed", "plan_not_allowed", "max")


async def test_sd3_a_consumer_plan_passes_when_the_org_admits_consumer_plans() -> None:
    result, _ = await _drive(Sandbox([login()]), Attempt([_code(work_only=False)]))
    assert result.plan == "max"


async def test_sd4_a_code_of_the_wrong_shape_is_answered_and_the_login_waits_on() -> None:
    sandbox = Sandbox([login()])
    attempt = Attempt([CodeRequest("FAKECODE123", False), _code(work_only=False)])
    result, _ = await _drive(sandbox, attempt)
    assert result.plan == "max"
    assert [(r.state, r.reason_code, r.retry) for r in attempt.replies] == [
        ("failed", "signin_code_invalid", True), ("signed_in", None, False)]
    assert sandbox.commands.count(LOGIN) == 1


async def test_sd5_a_rejected_code_restarts_the_login_with_a_new_link_then_gives_up() -> None:
    second = LINK.replace(STATE, "newstate")
    sandbox = Sandbox([login(1), login(1, url=second), login(1, url=second)])
    attempt = Attempt([_code(), CodeRequest("C#newstate", True), CodeRequest("C#newstate", True)])
    result, shown = await _drive(sandbox, attempt)
    assert isinstance(result, SignInRefused) and result.reason_code == "signin_failed"
    assert attempt.shown == [LINK, second, second]
    assert [(r.reason_code, r.retry) for r in attempt.replies] == [
        ("signin_failed", True), ("signin_failed", True), ("signin_failed", False)]
    assert shown[-1] == ("failed", "signin_failed", None)


async def test_sd6_a_link_off_the_signed_hosts_is_never_shown_and_the_login_is_killed() -> None:
    sandbox = Sandbox([login(url=LINK.replace("claude.com", "evil.example"))])
    process = sandbox.logins[0]
    attempt = Attempt([])
    result, shown = await _drive(sandbox, attempt)
    assert isinstance(result, SignInRefused) and result.reason_code == "signin_url_refused"
    assert attempt.shown == [] and shown == []
    assert process.killed


async def test_sd7_cancel_or_timeout_kills_the_login() -> None:
    for reason in ("signin_cancelled", "signin_timeout"):
        sandbox = Sandbox([login()])
        process = sandbox.logins[0]
        result, _ = await _drive(sandbox, Attempt([SignInRefused("x", reason=reason)]))
        assert isinstance(result, SignInRefused) and result.reason_code == reason
        assert process.killed and process.writes == []


async def test_sd8_sign_out_logs_out_then_wipes_and_never_raises() -> None:
    sandbox = Sandbox([])
    await sign_out(sandbox, own_login_launch(_launch()), HarnessLog.named("test"))  # type: ignore[arg-type]
    assert sandbox.commands[0] == LOGOUT
    assert sandbox.commands[1][:2] == ("sh", "-c") and "/state/claude" in sandbox.commands[1][2]

    class Broken(Sandbox):
        async def exec(self, argv: Sequence[str], *, env: Mapping[str, str], stdin: bool = False) -> Proc:
            raise RuntimeError("sandbox gone")

    await sign_out(Broken([]), own_login_launch(_launch()), HarnessLog.named("test"))  # type: ignore[arg-type]


async def test_sd9_the_wipe_checks_the_account_file_is_gone_too() -> None:
    """SIGNIN-5: every `claude` command writes `.claude.json` (the account's email): sign-out's last step checks it
    went with the credentials, so a kept sandbox's idle end leaves neither."""
    sandbox = Sandbox([])
    await sign_out(sandbox, own_login_launch(_launch()), HarnessLog.named("test"))  # type: ignore[arg-type]
    wipe = sandbox.commands[-1][2]
    assert f"test ! -e {CREDENTIALS}" in wipe and f"test ! -e {ACCOUNT_FILE}" in wipe
    assert ACCOUNT_FILE == "/state/claude/.claude.json"
    assert not any(c[0].endswith("claude") for c in sandbox.commands[1:])


async def test_sd10_parking_ends_every_process_but_the_ones_before_the_turn() -> None:
    """SIGNIN-5: a kept sandbox keeps none of a turn's processes; its own keep-alive (listed first) is spared."""

    class Listing(Sandbox):
        async def exec(self, argv: Sequence[str], *, env: Mapping[str, str], stdin: bool = False) -> Proc:
            self.commands.append(tuple(argv))
            self.envs.append(dict(env))
            if tuple(argv) == PROCESSES:
                return Proc(Process(b"1\n7\n8\n"))
            return Proc(Process(b"", exit_code=self.exit_code))

        exit_code: int = 0

    sandbox = Listing([])
    spare = await processes(sandbox)  # type: ignore[arg-type]
    assert spare == frozenset({1, 7, 8})
    assert await park(sandbox, spare, HarnessLog.named("test")) is True  # type: ignore[arg-type]
    assert sandbox.commands[-1] == PARK and sandbox.envs[-1]["SPARE"] == "1 7 8"
    assert "kill -9" in PARK[2] and "$$" in PARK[2]
    sandbox.exit_code = 1
    assert await park(sandbox, spare, HarnessLog.named("test")) is False  # type: ignore[arg-type]

    class Gone(Sandbox):
        async def exec(self, argv: Sequence[str], *, env: Mapping[str, str], stdin: bool = False) -> Proc:
            raise RuntimeError("sandbox gone")

    assert await park(Gone([]), spare, HarnessLog.named("test")) is False  # type: ignore[arg-type]


async def test_lk2_the_saved_transcript_never_holds_the_accounts_email() -> None:
    """Claude Code tells the model the signed-in account's email (seen live, SL-1): the state PipesHub saves
    names the account instead."""
    import io
    import tarfile

    from harness_engines.claude_code.session import REDACTED_ACCOUNT, ClaudeSession
    from harness_engines.claude_code.state import decode_state

    session_id = "11111111-2222-4333-8444-555555555555"
    line = json.dumps({"type": "user", "message": {"content": f"The user's email address is {RECORDED_EMAIL}."}})
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        data = (line + "\n").encode()
        info = tarfile.TarInfo(f"./{session_id}.jsonl")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))

    class Tars:
        id = "sbx-1"

        async def get_tar(self, src: str) -> AsyncIterator[bytes]:
            async def chunks() -> AsyncIterator[bytes]:
                yield buffer.getvalue()

            return chunks()

    session = ClaudeSession(
        engine=CLAUDE,
        sandbox=Tars(),  # type: ignore[arg-type]
        process=None,  # type: ignore[arg-type]
        stream=None,  # type: ignore[arg-type]
        reported_version="2.1.294",
        resume_session=session_id,
        secrets=frozenset(),
        log=HarnessLog.named("test"),
        own_login=True,
        redact=(RECORDED_EMAIL,),
    )
    state = await session.capture_state()
    restored = decode_state(state, CLAUDE)
    assert RECORDED_EMAIL.encode() not in restored.transcript
    assert REDACTED_ACCOUNT in restored.transcript
