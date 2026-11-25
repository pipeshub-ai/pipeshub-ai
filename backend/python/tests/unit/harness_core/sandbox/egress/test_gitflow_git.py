"""GIT-FLOW (C3.79, C3.80, C3.82, C6.10): a repository cloned in a setup phase and pushed in the agent phase.

Real git on both sides, no network: a bare repository served by ``git http-backend`` on loopback, the egress proxy
in-process between it and the sandbox's git client.

- GT-01 / SEC-EG-11: the clone runs under the setup policy (fetch only, read credential); the policy is switched to
  the agent phase's before anything else runs; no credential reaches the sandbox's environment, files or git config,
  and the setup placeholder is dead after the switch.
- GT-02: a push the person allowed goes only to the session branch; main and another repository are refused. The
  engine's own git in the agent phase fetches with the read credential and cannot push at all (WP-63 rule): a
  push presents a one-push grant's placeholder (GITF-19..GITF-22).
- GT-03: force-push, tag push and branch deletion are refused, and the remote is unchanged.
- GT-05: hooks never run and submodules are never fetched; each submodule URL is checked against the egress policy.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import os
import struct
import zlib
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from harness_core.clock import SystemClock
from harness_core.engines.git_checkout import (
    GitCheckout,
    GitSetupFailed,
    SealedGitCredentials,
    agent_git_env,
    agent_policy,
    clone,
    gitmodules,
    session_branch,
    setup_policy,
    submodule_report,
    verify_agent_phase,
)
from harness_core.sandbox.auth import StaticBearerAuth
from harness_core.sandbox.egress import (
    EgressBinding,
    EgressProxy,
    EgressRefusalReason,
    EgressRefused,
    InMemoryEgressBindings,
    InMemoryTokenSource,
    ProxyRequest,
    StaticResolver,
)
from harness_core.sandbox.egress.git import (
    ZERO_OID,
    check_git_request,
    decoded_body,
    fast_forward,
    pack_commits,
    parse_receive_pack,
)
from harness_core.sandbox.egress.policy import policy_errors
from harness_core.sandbox.egress.sealed import (
    CredentialSealer,
    RoutedTokenSource,
    SealedTokenSource,
    new_placeholder,
)
from harness_core.sandbox.egress.server import EgressServer
from harness_core.sandbox.types import EgressDestination, EgressPolicy, GitAccess
from harness_core.testing.ids import SeededIds
from tests.support.git_host import (
    GitHttpHost,
    HostSandbox,
    agit,
    bare_repository,
    commit,
    git,
    working_copy,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

R = EgressRefusalReason
HOST = "git.example.test"
REPO = "acme/widgets"
URL = f"http://{HOST}/{REPO}.git"
SESSION = "sess-1"
BRANCH = session_branch(SESSION)
SEALER = CredentialSealer(b"gitflow-deployment-secret")
READ_TOKEN = "x-access-token:read-token-0123456789abcdef"
PUSH_TOKEN = "x-access-token:push-token-0123456789abcdef"
SETUP_PH = "pxh-setup-0123456789abcdef0123456789"
PUSH_PH = "pxh-push-0123456789abcdef01234567890"
PROXY, SANDBOX = "127.0.0.2", "127.0.0.1"


def _proxy() -> EgressProxy:
    clock = SystemClock()
    return EgressProxy(
        bindings=InMemoryEgressBindings(),
        tokens=RoutedTokenSource(InMemoryTokenSource(b"t", clock), SealedTokenSource(SEALER, clock)),
        resolver=StaticResolver({}),
        upstreams={},
        clock=clock,
        ids=SeededIds(),
    )


def _checkout(upstream: str) -> GitCheckout:
    return GitCheckout(
        url=URL,
        session_branch=BRANCH,
        credentials=SealedGitCredentials(SEALER, read=READ_TOKEN, push=PUSH_TOKEN),
        upstream=upstream,
    )


def _setup(checkout: GitCheckout) -> EgressPolicy:
    return setup_policy(EgressPolicy(), checkout, SETUP_PH, SEALER.seal(SETUP_PH, READ_TOKEN))


def _agent(checkout: GitCheckout) -> EgressPolicy:
    # The agent phase fetches with the read credential (the controller's ``_git_phase``).
    return agent_policy(EgressPolicy(), checkout, PUSH_PH, SEALER.seal(PUSH_PH, READ_TOKEN))


class Stack:
    def __init__(self, tmp: Path, host: GitHttpHost, server: EgressServer, data_port: int, upstream: str) -> None:
        self.tmp = tmp
        self.host = host
        self.server = server
        self.proxy_url = f"http://{PROXY}:{data_port}"
        self.checkout = _checkout(upstream)
        self.workspace = tmp / "workspace"
        self.workspace.mkdir()
        self.sandbox = HostSandbox(tmp / "home", proxy_url=self.proxy_url)
        (tmp / "home").mkdir()
        self.bare = tmp / "srv" / f"{REPO}.git"
        self.refused: list[R] = []
        record = server.proxy.record_refusal

        def recorded(refused: EgressRefused, **kwargs: object) -> None:
            self.refused.append(refused.reason)
            record(refused, **kwargs)  # type: ignore[arg-type]

        server.proxy.record_refusal = recorded  # type: ignore[method-assign]

    async def bind(self, policy: EgressPolicy) -> None:
        await self.server.proxy.bindings.register(EgressBinding("sbx-git", PROXY, SANDBOX, policy, {"runId": "r1"}))

    async def setup(self) -> str:
        """Setup phase, then the switch to the agent phase, then the check (the order the controller runs them)."""
        await self.bind(_setup(self.checkout))
        head = await clone(self.sandbox, self.checkout, workspace_root=str(self.workspace), placeholder=SETUP_PH)
        await self.bind(_agent(self.checkout))
        await verify_agent_phase(
            self.sandbox, self.checkout, workspace_root=str(self.workspace), setup_placeholder=SETUP_PH
        )
        return head

    @property
    def clone_dir(self) -> Path:
        return self.workspace / self.checkout.directory

    async def agent_git(self, *args: str, check: bool = True) -> str:
        env = {k: v.replace("http://egress-proxy:8097", self.proxy_url) for k, v in agent_git_env(self.checkout, PUSH_PH).items()}
        return await agit(*args, cwd=self.clone_dir, env=env, check=check)

    def grant(self, *, repository: str = REPO, branch: str = BRANCH) -> str:
        """What the worker does once the person allowed a push: a fresh placeholder, good for one push."""
        placeholder = new_placeholder()
        binding = self.server.proxy.bindings.get("sbx-git")
        assert binding is not None
        _ = self.server.proxy.push_grants.issue(
            binding, placeholder=placeholder, host=HOST, repository=repository, branch=branch,
            credential_ref=SEALER.seal(placeholder, PUSH_TOKEN),
        )
        return placeholder

    async def git_with(self, placeholder: str, *args: str, check: bool = True) -> str:
        env = {
            k: v.replace("http://egress-proxy:8097", self.proxy_url)
            for k, v in agent_git_env(self.checkout, placeholder).items()
        }
        return await agit(*args, cwd=self.clone_dir, env=env, check=check)

    async def granted_git(self, *args: str, check: bool = True) -> str:
        """``git`` with a fresh one-push grant, as ``repository_push`` runs it after an approval."""
        return await self.git_with(self.grant(), *args, check=check)

    def remote(self, ref: str) -> str:
        return git("rev-parse", "--verify", "-q", ref, cwd=self.bare, check=False)


@pytest.fixture
async def stack(tmp_path: Path) -> AsyncIterator[Stack]:
    seed = working_copy(tmp_path / "seed")
    commit(seed, "README.md", "hello\n")
    git("tag", "v1", cwd=seed)
    _ = bare_repository(tmp_path / "srv", REPO, seed)
    host = GitHttpHost(tmp_path / "srv")
    upstream = await host.start()
    server = EgressServer(_proxy(), StaticBearerAuth("c"), instance="git")
    data_port, _ = await server.start(host=PROXY, data_port=0, control_port=0, control_host="127.0.0.1")
    try:
        yield Stack(tmp_path, host, server, data_port, upstream)
    finally:
        await server.stop()
        await host.stop()


def _secrets_in(stack: Stack) -> list[str]:
    found: list[str] = []
    for argv, env in stack.sandbox.execs:
        blob = " ".join(argv) + " " + " ".join(env.values())
        found += [t for t in (READ_TOKEN, PUSH_TOKEN, "read-token-", "push-token-") if t in blob]
    for root, _, files in os.walk(stack.workspace):
        for name in files:
            data = (stack.workspace / root / name).read_bytes()
            found += [t for t in ("read-token-", "push-token-") if t.encode() in data]
    return found


class TestGT01SetupPhase:
    async def test_gt01_the_clone_reads_with_the_injected_credential_and_leaves_none_behind(self, stack: Stack) -> None:
        head = await stack.setup()

        assert head == stack.remote("refs/heads/main")
        assert await stack.agent_git("rev-parse", "--abbrev-ref", "HEAD") == BRANCH
        # The host saw the real credential; the sandbox never held it, in env, argv, files or config.
        read = "Basic " + base64.b64encode(READ_TOKEN.encode()).decode()
        assert stack.host.authorizations and set(stack.host.authorizations) == {read}
        assert _secrets_in(stack) == []
        listed = git("config", "--local", "--list", cwd=stack.clone_dir)
        assert "extraheader" not in listed.lower() and "credential" not in listed and SETUP_PH not in listed
        assert "core.hookspath=/dev/null" in listed.lower()

    async def test_gt01_sec_eg_11_the_setup_placeholder_is_dead_once_the_agent_phase_starts(self, stack: Stack) -> None:
        _ = await stack.setup()
        stale = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.extraHeader"}
        from harness_core.engines.git_checkout import basic_auth

        stale["GIT_CONFIG_VALUE_0"] = f"Authorization: Basic {basic_auth(SETUP_PH)}"
        env = {"http_proxy": stack.proxy_url, **stale}
        out = await agit("ls-remote", URL, cwd=stack.clone_dir, env=env, check=False)
        assert out == ""
        assert stack.refused == [R.CALLER_AUTHORIZATION]
        seen = len(stack.host.authorizations)
        assert await stack.agent_git("ls-remote", URL, "refs/heads/main") != ""
        assert len(stack.host.authorizations) > seen

    async def test_gt01_the_setup_phase_may_not_push(self, stack: Stack) -> None:
        await stack.bind(_setup(stack.checkout))
        _ = await clone(stack.sandbox, stack.checkout, workspace_root=str(stack.workspace), placeholder=SETUP_PH)
        commit(stack.clone_dir, "a.txt")
        out = await stack.agent_git("push", "origin", f"HEAD:refs/heads/{BRANCH}", check=False)
        assert stack.remote(f"refs/heads/{BRANCH}") == "", out
        assert stack.refused == [R.GIT_OPERATION_NOT_ALLOWED]

    async def test_gt01_a_second_clone_into_a_workspace_that_holds_one_is_refused(self, stack: Stack) -> None:
        _ = await stack.setup()
        await stack.bind(_setup(stack.checkout))
        with pytest.raises(GitSetupFailed) as failed:
            _ = await clone(stack.sandbox, stack.checkout, workspace_root=str(stack.workspace), placeholder=SETUP_PH)
        assert failed.value.reason_code == "git_clone_failed"

    async def test_gt01_verify_fails_closed_when_a_credential_is_left_in_config(self, stack: Stack) -> None:
        _ = await stack.setup()
        git("config", "http.extraHeader", f"Authorization: Basic {SETUP_PH}", cwd=stack.clone_dir)
        with pytest.raises(GitSetupFailed) as failed:
            await verify_agent_phase(
                stack.sandbox, stack.checkout, workspace_root=str(stack.workspace), setup_placeholder=SETUP_PH
            )
        assert failed.value.reason_code == "git_setup_residue"


class TestGT02SessionBranchOnly:
    async def test_gt02_an_approved_push_to_the_session_branch_goes_through_with_the_push_credential(self, stack: Stack) -> None:
        _ = await stack.setup()
        first = commit(stack.clone_dir, "a.txt")
        _ = await stack.granted_git("push", "-q", "origin", BRANCH)
        assert stack.remote(f"refs/heads/{BRANCH}") == first
        second = commit(stack.clone_dir, "b.txt")
        _ = await stack.granted_git("push", "-q", "origin", BRANCH)
        assert stack.remote(f"refs/heads/{BRANCH}") == second
        assert _secrets_in(stack) == [] and stack.refused == []
        push = "Basic " + base64.b64encode(PUSH_TOKEN.encode()).decode()
        assert push in stack.host.authorizations

    async def test_gt02_a_push_to_main_or_another_branch_is_refused(self, stack: Stack) -> None:
        _ = await stack.setup()
        main = stack.remote("refs/heads/main")
        _ = commit(stack.clone_dir, "a.txt")
        for ref in ("refs/heads/main", "refs/heads/feature", f"refs/heads/{BRANCH}-other"):
            out = await stack.granted_git("push", "origin", f"HEAD:{ref}", check=False)
            assert "403" in out or stack.remote(ref) in ("", main)
        assert stack.remote("refs/heads/main") == main
        assert stack.remote("refs/heads/feature") == ""
        assert stack.refused == [R.GIT_REF_NOT_ALLOWED] * 3

    async def test_gt02_a_push_naming_both_the_session_branch_and_main_is_refused_whole(self, stack: Stack) -> None:
        _ = await stack.setup()
        main = stack.remote("refs/heads/main")
        _ = commit(stack.clone_dir, "a.txt")
        _ = await stack.granted_git("push", "--atomic", "origin", f"HEAD:refs/heads/{BRANCH}", "HEAD:refs/heads/main", check=False)
        assert stack.remote("refs/heads/main") == main and stack.remote(f"refs/heads/{BRANCH}") == ""
        assert stack.refused == [R.GIT_REF_NOT_ALLOWED]

    async def test_gt02_another_repository_on_the_same_host_is_unreachable(self, stack: Stack) -> None:
        _ = await stack.setup()
        other = f"http://{HOST}/acme/other.git"
        assert await stack.agent_git("ls-remote", other, check=False) == ""
        assert not any("/acme/other" in p for p in stack.host.paths)
        assert stack.refused == [R.GIT_OPERATION_NOT_ALLOWED]


class TestGITFOnePushGrants:
    async def test_gitf19_the_engines_own_git_fetches_and_cannot_push(self, stack: Stack) -> None:
        _ = await stack.setup()
        _ = commit(stack.clone_dir, "a.txt")
        assert await stack.agent_git("ls-remote", URL, "refs/heads/main") != ""
        out = await stack.agent_git("push", "origin", BRANCH, check=False)
        assert stack.remote(f"refs/heads/{BRANCH}") == "", out
        assert stack.refused == [R.GIT_OPERATION_NOT_ALLOWED]
        # Its fetches carry the read credential, never the push one.
        push = "Basic " + base64.b64encode(PUSH_TOKEN.encode()).decode()
        assert push not in stack.host.authorizations

    async def test_gitf20_an_approved_push_goes_once_and_a_replay_of_its_placeholder_is_refused(self, stack: Stack) -> None:
        _ = await stack.setup()
        first = commit(stack.clone_dir, "a.txt")
        placeholder = stack.grant()
        _ = await stack.git_with(placeholder, "push", "-q", "origin", BRANCH)
        assert stack.remote(f"refs/heads/{BRANCH}") == first
        push = "Basic " + base64.b64encode(PUSH_TOKEN.encode()).decode()
        assert push in stack.host.authorizations and _secrets_in(stack) == []
        _ = commit(stack.clone_dir, "b.txt")
        out = await stack.git_with(placeholder, "push", "-q", "origin", BRANCH, check=False)
        assert stack.remote(f"refs/heads/{BRANCH}") == first, out
        assert stack.refused == [R.TOKEN_REPLAYED]

    async def test_gitf21_a_placeholder_approved_for_repository_a_is_refused_at_repository_b(self, stack: Stack) -> None:
        _ = await stack.setup()
        _ = commit(stack.clone_dir, "a.txt")
        placeholder = stack.grant()
        other = f"http://{HOST}/acme/other.git"
        out = await stack.git_with(placeholder, "push", other, f"HEAD:refs/heads/{BRANCH}", check=False)
        assert not any("/acme/other" in p for p in stack.host.paths), out
        assert stack.refused == [R.GIT_OPERATION_NOT_ALLOWED]
        # And a grant naming a repository the sandbox's git rule does not hold is never issued.
        from harness_core.sandbox.egress.push_grants import PushGrantInvalid

        with pytest.raises(PushGrantInvalid):
            _ = stack.grant(repository="acme/other")

    async def test_gitf22_a_grant_is_for_its_branch_only(self, stack: Stack) -> None:
        _ = await stack.setup()
        _ = commit(stack.clone_dir, "a.txt")
        out = await stack.git_with(stack.grant(), "push", "origin", "HEAD:refs/heads/main", check=False)
        assert stack.remote("refs/heads/main") != git("rev-parse", "HEAD", cwd=stack.clone_dir), out
        assert stack.refused == [R.GIT_REF_NOT_ALLOWED]


class TestGT03Refusals:
    async def test_gt03_force_push_is_refused_and_the_branch_keeps_its_commit(self, stack: Stack) -> None:
        _ = await stack.setup()
        pushed = commit(stack.clone_dir, "a.txt")
        _ = await stack.granted_git("push", "-q", "origin", BRANCH)
        git("commit", "-q", "--amend", "-m", "rewritten", cwd=stack.clone_dir)
        _ = await stack.granted_git("push", "-f", "origin", BRANCH, check=False)
        assert stack.remote(f"refs/heads/{BRANCH}") == pushed
        assert stack.refused[-1] is R.GIT_FORCE_PUSH_REFUSED
        git("reset", "-q", "--hard", "HEAD~1", cwd=stack.clone_dir)
        _ = await stack.granted_git("push", "-f", "origin", BRANCH, check=False)
        assert stack.remote(f"refs/heads/{BRANCH}") == pushed
        assert stack.refused[-1] is R.GIT_FORCE_PUSH_REFUSED

    async def test_gt03_tag_push_is_refused(self, stack: Stack) -> None:
        _ = await stack.setup()
        _ = commit(stack.clone_dir, "a.txt")
        git("tag", "v2", cwd=stack.clone_dir)
        git("tag", "-f", "v1", cwd=stack.clone_dir)
        _ = await stack.granted_git("push", "origin", "v2", check=False)
        _ = await stack.granted_git("push", "-f", "origin", "refs/tags/v1", check=False)
        assert stack.remote("refs/tags/v2") == ""
        assert stack.remote("refs/tags/v1") == git("rev-parse", "main", cwd=stack.bare)
        assert stack.refused == [R.GIT_REF_NOT_ALLOWED] * 2

    async def test_gt03_deleting_a_branch_is_refused(self, stack: Stack) -> None:
        _ = await stack.setup()
        pushed = commit(stack.clone_dir, "a.txt")
        _ = await stack.granted_git("push", "-q", "origin", BRANCH)
        _ = await stack.granted_git("push", "origin", "--delete", BRANCH, check=False)
        _ = await stack.granted_git("push", "origin", "--delete", "main", check=False)
        assert stack.remote(f"refs/heads/{BRANCH}") == pushed
        assert stack.remote("refs/heads/main") != ""
        assert stack.refused == [R.GIT_DELETE_REFUSED, R.GIT_REF_NOT_ALLOWED]


class TestGT05MaliciousRepository:
    @pytest.fixture
    async def hostile(self, tmp_path: Path, stack: Stack) -> Stack:
        work = tmp_path / "hostile"
        git("clone", "-q", str(stack.bare), str(work))
        (work / ".gitmodules").write_text(
            '[submodule "ext"]\n\tpath = ext\n\turl = ext::sh -c touch% /tmp/pxh-gitflow-pwned\n'
            '[submodule "local"]\n\tpath = local\n\turl = file:///etc\n'
            '[submodule "evil"]\n\tpath = evil\n\turl = https://evil.example/x.git\n'
            f'[submodule "same"]\n\tpath = same\n\turl = {URL}\n'
        )
        (work / ".gitattributes").write_text("*.bin filter=lfs diff=lfs merge=lfs -text\n")
        (work / "big.bin").write_text("version https://git-lfs.github.com/spec/v1\noid sha256:" + "0" * 64 + "\nsize 1\n")
        git("add", "-A", cwd=work)
        git("commit", "-q", "-m", "hostile", cwd=work)
        git("push", "-q", "origin", "HEAD:main", cwd=work)
        return stack

    async def test_gt05_submodules_are_never_fetched_and_each_url_is_checked_against_egress(self, hostile: Stack) -> None:
        _ = await hostile.setup()
        for name in ("ext", "local", "evil", "same"):
            path = hostile.clone_dir / name
            assert not path.exists() or not any(path.iterdir())
        assert not os.path.exists("/tmp/pxh-gitflow-pwned")
        report = submodule_report(
            await gitmodules(hostile.sandbox, hostile.checkout, workspace_root=str(hostile.workspace)),
            _agent(hostile.checkout),
        )
        assert {c.name: c.reachable for c in report} == {"ext": False, "local": False, "evil": False, "same": True}
        # The agent's own git does not recurse into them either.
        _ = await hostile.agent_git("submodule", "update", "--init", "local", check=False)
        assert not (hostile.clone_dir / "local" / "passwd").exists()

    async def test_gt05_lfs_pointers_stay_pointers(self, hostile: Stack) -> None:
        _ = await hostile.setup()
        assert (hostile.clone_dir / "big.bin").read_text().startswith("version https://git-lfs")

    async def test_gt05_a_hook_in_the_workspace_never_runs_on_checkout_or_commit(self, stack: Stack) -> None:
        _ = await stack.setup()
        assert not any((stack.clone_dir / ".git" / "hooks").glob("*")) if (stack.clone_dir / ".git" / "hooks").exists() else True
        marker = stack.tmp / "hook-ran"
        hooks = stack.clone_dir / ".git" / "hooks"
        hooks.mkdir(exist_ok=True)
        for name in ("post-checkout", "pre-commit", "pre-push"):
            hook = hooks / name
            hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
            hook.chmod(0o755)
        _ = await stack.agent_git("checkout", "-q", "-b", "scratch")
        _ = await stack.agent_git("commit", "-q", "--allow-empty", "-m", "x")
        assert not marker.exists()


# Units of the push rule, on packs real git writes.


def _push_body(updates: list[tuple[str, str, str]], pack: bytes) -> bytes:
    lines = b""
    for index, (old, new, ref) in enumerate(updates):
        text = f"{old} {new} {ref}".encode() + (b"\0report-status ofs-delta" if index == 0 else b"") + b"\n"
        lines += f"{len(text) + 4:04x}".encode() + text
    return lines + b"0000" + pack


def _pack(work: Path, *revs: str) -> bytes:
    import subprocess

    result = subprocess.run(
        ["git", "pack-objects", "--revs", "--stdout"], cwd=work, input="\n".join(revs).encode() + b"\n",
        capture_output=True, check=True, env={"PATH": os.environ["PATH"], "GIT_CONFIG_NOSYSTEM": "1"},
    )
    return result.stdout


@pytest.fixture
def history(tmp_path: Path) -> tuple[Path, list[str]]:
    work = working_copy(tmp_path / "h")
    commits = [commit(work, "f.txt", "line\n" * 200 + f"{n}\n") for n in range(6)]
    return work, commits


ACCESS = GitAccess(REPO, push_branch=BRANCH)


def _receive(body: bytes) -> ProxyRequest:
    return ProxyRequest("POST", f"/{REPO}.git/git-receive-pack", (("Host", HOST),), body)


class TestPushRuleUnits:
    def test_gt03_pack_walk_proves_a_fast_forward_and_nothing_else(self, history: tuple[Path, list[str]]) -> None:
        work, commits = history
        pack = _pack(work, commits[5], f"^{commits[2]}")
        found = pack_commits(pack)
        assert set(found) == set(commits[3:])
        assert fast_forward(found, commits[2], commits[5])
        assert not fast_forward(found, commits[1] + "", "f" * 40)
        assert not fast_forward(found, "a" * 40, commits[5])

    def test_gt03_deltified_commits_are_resolved(self, history: tuple[Path, list[str]]) -> None:
        work, commits = history
        git("repack", "-adf", "--window=50", "--depth=50", cwd=work)
        packed = next((work / ".git" / "objects" / "pack").glob("*.pack")).read_bytes()
        assert set(commits) <= set(pack_commits(packed))

    def test_gt03_a_pack_that_does_not_check_out_is_refused(self, history: tuple[Path, list[str]]) -> None:
        work, commits = history
        pack = bytearray(_pack(work, commits[1]))
        pack[20] ^= 0xFF
        with pytest.raises(EgressRefused) as refused:
            _ = pack_commits(bytes(pack))
        assert refused.value.reason is R.GIT_PUSH_UNREADABLE

    def test_gt03_a_forged_parent_does_not_count(self, history: tuple[Path, list[str]]) -> None:
        """A commit object claiming the old tip as parent is only one if its own id is the hash of that content."""
        old = history[1][3]
        content = f"tree {'4b825dc642cb6eb9a060e54bf8d69288fbee4904'}\nparent {old}\nauthor a <a> 0 +0000\ncommitter a <a> 0 +0000\n\nx\n".encode()
        packed = _single_object_pack(1, content)
        found = pack_commits(packed)
        real = hashlib.sha1(b"commit %d\0" % len(content) + content).hexdigest()
        assert found == {real: (old,)}
        body = _push_body([(old, "e" * 40, f"refs/heads/{BRANCH}")], packed)
        with pytest.raises(EgressRefused) as refused:
            check_git_request(_receive(body), f"/{REPO}.git/git-receive-pack", ACCESS)
        assert refused.value.reason is R.GIT_FORCE_PUSH_REFUSED

    @pytest.mark.parametrize(
        ("updates", "reason"),
        [
            ([(ZERO_OID, "a" * 40, "refs/heads/main")], R.GIT_REF_NOT_ALLOWED),
            ([(ZERO_OID, "a" * 40, "refs/tags/v1")], R.GIT_REF_NOT_ALLOWED),
            ([("a" * 40, ZERO_OID, f"refs/heads/{BRANCH}")], R.GIT_DELETE_REFUSED),
            ([("a" * 40, "b" * 40, f"refs/heads/{BRANCH}")], R.GIT_FORCE_PUSH_REFUSED),
            ([(ZERO_OID, "a" * 40, f"refs/heads/{BRANCH}"), (ZERO_OID, "a" * 40, "refs/heads/x")], R.GIT_REF_NOT_ALLOWED),
            ([], R.GIT_PUSH_UNREADABLE),
        ],
    )
    def test_gt03_ref_updates_outside_the_rule_are_refused(
        self, updates: list[tuple[str, str, str]], reason: R
    ) -> None:
        with pytest.raises(EgressRefused) as refused:
            check_git_request(_receive(_push_body(updates, b"")), f"/{REPO}.git/git-receive-pack", ACCESS)
        assert refused.value.reason is reason

    def test_gt03_creating_the_session_branch_needs_no_pack_walk(self) -> None:
        body = _push_body([(ZERO_OID, "a" * 40, f"refs/heads/{BRANCH}")], b"")
        check_git_request(_receive(body), f"/{REPO}.git/git-receive-pack", ACCESS)

    @pytest.mark.parametrize(
        "body",
        [
            b"00a0push-cert\0report-status\n",
            b"0010not a pkt line",
            b"zzzz",
            _push_body([(ZERO_OID, "a" * 64, f"refs/heads/{BRANCH}")], b""),
            _push_body([(ZERO_OID, "a" * 40, f"refs/heads/{BRANCH}")], b"0000extra"),
        ],
    )
    def test_gt03_a_push_the_rule_cannot_read_is_refused(self, body: bytes) -> None:
        with pytest.raises(EgressRefused) as refused:
            check_git_request(_receive(body), f"/{REPO}.git/git-receive-pack", ACCESS)
        assert refused.value.reason is R.GIT_PUSH_UNREADABLE

    @pytest.mark.parametrize(
        ("method", "path", "access"),
        [
            ("GET", f"/{REPO}.git/info/refs?service=git-receive-pack", GitAccess(REPO)),
            ("POST", f"/{REPO}.git/git-receive-pack", GitAccess(REPO)),
            ("GET", f"/{REPO}.git/info/refs", ACCESS),
            ("GET", f"/{REPO}.git/HEAD", ACCESS),
            ("GET", f"/{REPO}.git/objects/info/packs", ACCESS),
            ("POST", f"/{REPO}.git/info/lfs/objects/batch", ACCESS),
            ("GET", "/api/v3/repos/acme/widgets", ACCESS),
            ("GET", "/acme/other.git/info/refs?service=git-upload-pack", ACCESS),
            ("GET", "/acme/widgets-evil.git/info/refs?service=git-upload-pack", ACCESS),
            ("GET", f"/{REPO}.git/info/refs?service=git-upload-pack&x=1", ACCESS),
            ("GET", "/acme%2fwidgets.git/info/refs?service=git-upload-pack", ACCESS),
            ("POST", f"/{REPO}.git/git-upload-pack?x", ACCESS),
        ],
    )
    def test_gt02_only_the_repositorys_smart_protocol_is_reachable(self, method: str, path: str, access: GitAccess) -> None:
        with pytest.raises(EgressRefused) as refused:
            check_git_request(ProxyRequest(method, path, ()), path, access)
        assert refused.value.reason is R.GIT_OPERATION_NOT_ALLOWED

    def test_gt02_a_gzip_fetch_negotiation_is_read_decoded(self) -> None:
        plain = b"0032want " + b"a" * 40 + b"\n0000"
        request = ProxyRequest("POST", "/x", (("Content-Encoding", "gzip"),), gzip.compress(plain))
        assert decoded_body(request) == plain
        bomb = ProxyRequest("POST", "/x", (("Content-Encoding", "gzip"),), gzip.compress(b"\0" * (65 * 1024 * 1024)))
        with pytest.raises(EgressRefused) as refused:
            _ = decoded_body(bomb)
        assert refused.value.reason is R.BODY_TOO_LARGE

    def test_gt02_a_git_destination_with_a_bad_repository_or_branch_is_not_bound(self) -> None:
        for access in (GitAccess("../x"), GitAccess("a/b.git"), GitAccess(REPO, push_branch="-x"), GitAccess(REPO, push_branch="a..b")):
            assert policy_errors(EgressPolicy(destinations=(EgressDestination(HOST, (443,), git=access),)))
        assert policy_errors(EgressPolicy(destinations=(EgressDestination(HOST, (443,), git=ACCESS),))) == []

    def test_gt01_setup_and_agent_policies_differ_only_in_their_route(self) -> None:
        checkout = _checkout("http://127.0.0.1:1")
        setup, agent = _setup(checkout), _agent(checkout)
        assert [d.host for d in setup.destinations] == [d.host for d in agent.destinations] == [HOST]
        # GITF-19: the agent phase fetches only; a push needs a one-push grant (WP-63 rule).
        assert setup.destinations[0].git == GitAccess(REPO) and agent.destinations[0].git == GitAccess(REPO)
        assert [r.placeholder for r in setup.token_routes] == [SETUP_PH]
        assert [r.placeholder for r in agent.token_routes] == [PUSH_PH]
        assert replace(setup.destinations[0], git=None) == replace(agent.destinations[0], git=None)


def _single_object_pack(kind: int, content: bytes) -> bytes:
    size = len(content)
    header = bytearray([(kind << 4) | (size & 0x0F) | (0x80 if size > 15 else 0)])
    size >>= 4
    while size:
        header.append((size & 0x7F) | (0x80 if size > 0x7F else 0))
        size >>= 7
    body = b"PACK" + struct.pack(">II", 2, 1) + bytes(header) + zlib.compress(content)
    return body + hashlib.sha1(body).digest()


def test_gt03_receive_pack_parsing_keeps_every_update() -> None:
    body = _push_body([(ZERO_OID, "a" * 40, "refs/heads/x"), ("b" * 40, "c" * 40, "refs/heads/y")], b"PACKrest")
    parsed = parse_receive_pack(body)
    assert [u.ref for u in parsed.updates] == ["refs/heads/x", "refs/heads/y"] and parsed.pack == b"PACKrest"
