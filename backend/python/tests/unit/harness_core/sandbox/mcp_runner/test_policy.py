"""The runner's pure decisions, table-driven (P5.10: MR-5, MR-9, MS-3, MW-7 helpers)."""

from __future__ import annotations

import pytest

from harness_core.sandbox.mcp_runner import (
    MCPRunnerSpec,
    RunnerEgressHost,
    RunnerEgressInvalid,
    RunnerKey,
    RunnerSecrets,
    RunnerSpecInvalid,
    RunnerUnpinned,
    fetching_launcher,
    parse_egress_host,
    runner_egress_policy,
)
from harness_core.sandbox.mcp_runner.policy import check_spec, secrets_fingerprint
from harness_core.sandbox.types import EgressDefault, EgressPolicy


def spec(**changes: object) -> MCPRunnerSpec:
    fields: dict[str, object] = {"server_id": "s", "image": "img", "command": "server", "env_names": frozenset({"TOKEN"})}
    return MCPRunnerSpec(**{**fields, **changes})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("command", "args", "expected"),
    [
        ("npx", ["-y", "pkg@1.0.0"], "npx"),
        ("/usr/local/bin/npx", ["pkg"], "npx"),
        ("uvx", ["pkg"], "uvx"),
        ("bunx", ["pkg"], "bunx"),
        ("pnpx", ["pkg"], "pnpx"),
        ("npm", ["exec", "pkg"], "npm exec"),
        ("npm", ["x", "pkg"], "npm x"),
        ("pnpm", ["dlx", "pkg"], "pnpm dlx"),
        ("yarn", ["dlx", "pkg"], "yarn dlx"),
        ("pipx", ["run", "pkg"], "pipx run"),
        ("uv", ["tool", "run", "pkg"], "uv tool run"),
        ("bun", ["x", "pkg"], "bun x"),
        ("deno", ["run", "npm:pkg"], "deno remote"),
        ("deno", ["run", "https://example.com/x.ts"], "deno remote"),
        ("node", ["/app/server.js"], None),
        ("python3", ["-m", "server"], None),
        ("npm", ["start"], None),
        ("uv", ["run", "server.py"], None),
        ("deno", ["run", "/app/main.ts"], None),
        ("/opt/server/bin/mcp-server", [], None),
    ],
)
def test_fetching_launchers(command: str, args: list[str], expected: str | None) -> None:
    assert fetching_launcher(command, args) == expected


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"command": "npx", "args": ("-y", "pkg")}, RunnerUnpinned),
        ({"command": ""}, RunnerSpecInvalid),
        ({"image": " "}, RunnerSpecInvalid),
        ({"args": ("a\x00b",)}, RunnerSpecInvalid),
        ({"env_names": frozenset({"BAD-NAME"})}, RunnerSpecInvalid),
        ({"env_names": frozenset({"LD_PRELOAD"})}, RunnerSpecInvalid),
        ({"env_names": frozenset({"HTTP_PROXY"})}, RunnerSpecInvalid),
        ({"env_names": frozenset({"PATH"})}, RunnerSpecInvalid),
    ],
)
def test_check_spec_refusals(changes: dict[str, object], error: type[Exception]) -> None:
    with pytest.raises(error):
        check_spec(spec(**changes))


def test_check_spec_accepts_a_pinned_server() -> None:
    check_spec(spec(command="node", args=("/app/server.js",)))


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ("api.example.com", RunnerEgressHost("api.example.com", (80,))),
        ("api.example.com:8080", RunnerEgressHost("api.example.com", (8080,))),
        (" api.example.com ", RunnerEgressHost("api.example.com", (80,))),
    ],
)
def test_parse_egress_host(entry: str, expected: RunnerEgressHost) -> None:
    assert parse_egress_host(entry) == expected


@pytest.mark.parametrize("entry", ["api.example.com:0", "api.example.com:99999", "api.example.com:http"])
def test_parse_egress_host_refuses_a_bad_port(entry: str) -> None:
    with pytest.raises(RunnerEgressInvalid):
        _ = parse_egress_host(entry)


def test_no_hosts_is_deny_and_hosts_are_exactly_the_allowlist() -> None:
    assert runner_egress_policy(spec()) == EgressPolicy()
    policy = runner_egress_policy(spec(egress=(RunnerEgressHost("a.example", (80, 8080)),)))
    assert policy.default is EgressDefault.DENY and policy.token_routes == ()
    assert [(d.host, d.ports, d.upstream) for d in policy.destinations] == [("a.example", (80, 8080), None)]


@pytest.mark.parametrize("host", ["bad host", "a/b", "user@a.example", ""])
def test_a_malformed_host_is_egress_invalid(host: str) -> None:
    with pytest.raises(RunnerEgressInvalid):
        _ = runner_egress_policy(spec(egress=(RunnerEgressHost(host),)))


def test_secrets_never_show_and_fingerprints_tell_rotation_apart() -> None:
    secrets = RunnerSecrets({"TOKEN": "s3cret-value"})
    assert "s3cret-value" not in repr(secrets) and "s3cret-value" not in str(secrets)
    key = b"k" * 32
    same = secrets_fingerprint(RunnerSecrets({"TOKEN": "s3cret-value"}), key)
    assert secrets_fingerprint(secrets, key) == same
    assert secrets_fingerprint(RunnerSecrets({"TOKEN": "rotated"}), key) != same
    assert "s3cret-value" not in same


def test_the_key_digest_names_the_owner() -> None:
    assert RunnerKey("o", "s", "alice").digest() != RunnerKey("o", "s", "bob").digest()
    assert RunnerKey("o", "s", "alice").digest() == RunnerKey("o", "s", "alice").digest()


def test_the_spec_digest_changes_with_what_runs() -> None:
    base = spec()
    assert base.digest() == spec().digest()
    for changed in (spec(args=("x",)), spec(image="other"), spec(egress=(RunnerEgressHost("a.example"),))):
        assert changed.digest() != base.digest()
