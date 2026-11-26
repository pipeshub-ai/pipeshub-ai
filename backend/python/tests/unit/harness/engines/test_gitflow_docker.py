"""GT-01 / SEC-EG-11 on the real lane (runc): a repository cloned in an engine image's sandbox behind the real egress
proxy, the setup phase switched off before anything else runs, and pushes held to the session branch.

GITF-D6 (WP-63 rule): the engine's own git fetches and cannot push; a push goes only with a one-push grant the
worker registers at the real proxy's control plane after the person's approval, for a fresh placeholder: it pushes
once, its replay is refused, and it is refused at another repository.

The release opencode run image (``pxh-opencode-tools``) under ``DockerSandboxBroker``, the egress proxy in its
container (``real_stack``) opening sealed routes with the deployment key, and a bare repository served by
``git http-backend`` on the rig bridge. Nothing leaves the host.
"""

from __future__ import annotations

import base64
import uuid
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from harness_core.engines.controller import engine_mode
from harness_core.engines.git_checkout import (
    GitCheckout,
    SealedGitCredentials,
    agent_git_env,
    agent_policy,
    clone,
    phase_credential,
    push_session_branch,
    session_branch,
    setup_policy,
    verify_agent_phase,
)
from harness_core.sandbox.auth import StaticBearerAuth
from harness_core.sandbox.egress.client import RemoteEgressBindings
from harness_core.sandbox.egress.sealed import CredentialSealer, new_placeholder
from harness_core.sandbox.types import EgressPolicy, LeaseRef, MountSpec, SandboxSpec
from tests.support.git_host import (
    GitHttpHost,
    bare_repository,
    commit,
    git,
    working_copy,
)
from tests.unit.harness.engines.real_stack import (
    IMAGE,
    engine_broker,
    lane_available,
    start_stack,
    stop_stack,
)
from tests.unit.harness.sandbox_broker.conftest import cleanup, cleanup_networks

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator, Mapping
    from pathlib import Path

    from app.harness.sandbox.docker_broker import DockerSandboxBroker
    from harness_core.sandbox.ports import SandboxHandle
    from tests.unit.harness.engines.real_stack import RealStack

pytestmark = [pytest.mark.docker, pytest.mark.timeout(600, method="thread")]

SEALING_KEY = uuid.uuid4().hex
READ_TOKEN = "x-access-token:read-tok-" + uuid.uuid4().hex
PUSH_TOKEN = "x-access-token:push-tok-" + uuid.uuid4().hex
HOST = "git.example.test"
URL = f"http://{HOST}/acme/widgets.git"
SESSION = "sess-docker-1"
BRANCH = session_branch(SESSION)
WORKSPACE = "/workspace"
SETUP_PH = "pxh-setup-" + uuid.uuid4().hex
PUSH_PH = "pxh-push-" + uuid.uuid4().hex
IDENTITY = "-c user.name=engine -c user.email=engine@example.test"


@pytest.fixture(scope="module")
def git_stack() -> Iterator[RealStack]:
    available, reason = lane_available()
    if not available:
        pytest.skip(reason)
    stack = start_stack(upstreams=lambda _s: {"EGRESS_TEST_SEALING_KEY": SEALING_KEY})
    yield stack
    stop_stack(stack)


@pytest.fixture
async def host(git_stack: RealStack, tmp_path: Path) -> AsyncIterator[tuple[GitHttpHost, str]]:
    seed = working_copy(tmp_path / "seed")
    commit(seed, "README.md", "hello\n")
    _ = bare_repository(tmp_path / "srv", "acme/widgets", seed)
    _ = bare_repository(tmp_path / "srv", "acme/other", seed)
    served = GitHttpHost(tmp_path / "srv")
    upstream = await served.start(git_stack.host_ip)
    yield served, upstream
    await served.stop()


@pytest.fixture
async def broker(git_stack: RealStack) -> AsyncIterator[DockerSandboxBroker]:
    namespace = f"io.pipeshub.gitflow.{uuid.uuid4().hex[:8]}"
    prefix = "pxh-gf-" + namespace.rsplit(".", 1)[1]
    created, provider = await engine_broker(git_stack, namespace, prefix)
    yield created
    await created.aclose()
    await provider.run_blocking(cleanup, provider, namespace, prefix)
    await provider.run_blocking(cleanup_networks, provider, namespace, git_stack.proxy)
    provider.close()


def _basic(credential: str) -> str:
    return "Basic " + base64.b64encode(credential.encode()).decode()


async def _sh(sandbox: SandboxHandle, script: str, env: Mapping[str, str] | None = None) -> tuple[int, str]:
    process = await sandbox.exec(["sh", "-c", script], env={"HOME": "/tmp", **(env or {})})
    out = b"".join([chunk async for chunk in process.stdout])
    err = b"".join([chunk async for chunk in process.stderr])
    return await process.wait(timeout_s=120), (out + err).decode("utf-8", "replace")


async def test_gt01_sec_eg_11_a_clone_in_the_engine_image_leaves_no_token_and_pushes_only_its_branch(
    broker: DockerSandboxBroker, host: tuple[GitHttpHost, str], tmp_path: Path, git_stack: RealStack
) -> None:
    served, upstream = host
    checkout = GitCheckout(
        url=URL,
        session_branch=BRANCH,
        credentials=SealedGitCredentials(CredentialSealer(SEALING_KEY.encode()), read=READ_TOKEN, push=PUSH_TOKEN),
        upstream=upstream,
    )
    mode = engine_mode(broker.modes())
    assert mode is not None
    base = SandboxSpec(
        image_digest=IMAGE, mode=mode, mounts=(MountSpec(WORKSPACE),), labels={"pxh.test": "gitflow"}
    )
    setup = replace(
        base, egress=setup_policy(EgressPolicy(), checkout, SETUP_PH, await phase_credential(checkout, "setup", SETUP_PH))
    )
    # The agent phase fetches with the read credential, as the controller binds it (GITF-19).
    agent = replace(
        base, egress=agent_policy(EgressPolicy(), checkout, PUSH_PH, await phase_credential(checkout, "setup", PUSH_PH))
    )
    lease, key = LeaseRef("worker-1", 1), f"gitflow-{uuid.uuid4().hex[:8]}"

    sandbox = await broker.acquire(setup, lease=lease, idempotency_key=key)
    try:
        head = await clone(sandbox, checkout, workspace_root=WORKSPACE, placeholder=SETUP_PH)
        switched = await broker.acquire(agent, lease=lease, idempotency_key=key)
        assert switched.id == sandbox.id
        await verify_agent_phase(sandbox, checkout, workspace_root=WORKSPACE, setup_placeholder=SETUP_PH)

        bare = tmp_path / "srv" / "acme" / "widgets.git"
        assert head == git("rev-parse", "main", cwd=bare)
        assert served.authorizations and set(served.authorizations) == {_basic(READ_TOKEN)}

        # GT-01: no token anywhere the sandbox can read, and no credential in the clone's config.
        _, found = await _sh(
            sandbox,
            f'grep -rlsF -e read-tok- -e push-tok- / --exclude-dir=proc --exclude-dir=sys --exclude-dir=dev; '
            f'env | grep -F -e read-tok- -e push-tok-; git -C {WORKSPACE}/repo config --local --list',
        )
        assert "read-tok-" not in found and "push-tok-" not in found, found
        assert "extraheader" not in found.lower() and "credential" not in found and SETUP_PH not in found

        # SEC-EG-11: the setup placeholder is dead once the agent phase is bound.
        code, out = await _sh(sandbox, f"git -C {WORKSPACE}/repo ls-remote origin", agent_git_env(checkout, SETUP_PH))
        assert code != 0, out

        # GITF-D6: the engine's own git environment fetches and cannot push.
        env = agent_git_env(checkout, PUSH_PH)
        repo = f"{WORKSPACE}/repo"
        bare = tmp_path / "srv" / "acme" / "widgets.git"
        code, out = await _sh(sandbox, f"cd {repo} && git fetch -q origin && echo x > a.txt && git add a.txt && git {IDENTITY} commit -q -m a", env)
        assert code == 0, out
        code, out = await _sh(sandbox, f"cd {repo} && git push -q origin HEAD", env)
        assert code != 0, out
        assert git("rev-parse", "--verify", "-q", f"refs/heads/{BRANCH}", cwd=bare, check=False) == ""
        assert _basic(PUSH_TOKEN) not in served.authorizations

        # The person approved: a fresh placeholder, granted for one push at the real proxy's control plane.
        control = RemoteEgressBindings(git_stack.control_url, StaticBearerAuth(git_stack.control_token))

        async def grant(repository: str = "acme/widgets") -> str:
            placeholder = new_placeholder()
            sealed = await phase_credential(checkout, "agent", placeholder)
            assert sealed is not None
            await control.grant_push(
                sandbox_id=sandbox.id, placeholder=placeholder, host=HOST, repository=repository, branch=BRANCH,
                credential_ref=sealed, ttl_s=120,
            )
            return placeholder

        try:
            approved = await grant()
            pushed = await push_session_branch(sandbox, checkout, workspace_root=WORKSPACE, placeholder=approved)
            assert pushed.pushed, pushed.detail
            first = git("rev-parse", f"refs/heads/{BRANCH}", cwd=bare)
            assert pushed.commit == first and _basic(PUSH_TOKEN) in served.authorizations
            # The same placeholder again: a replay, refused at the proxy; the branch keeps its commit.
            code, out = await _sh(sandbox, f"cd {repo} && echo y > b.txt && git add b.txt && git {IDENTITY} commit -q -m b", env)
            assert code == 0, out
            replayed = await push_session_branch(sandbox, checkout, workspace_root=WORKSPACE, placeholder=approved)
            assert not replayed.pushed and approved not in replayed.detail
            assert git("rev-parse", f"refs/heads/{BRANCH}", cwd=bare) == first
            # A placeholder approved for acme/widgets, presented at acme/other: refused, never forwarded.
            other = await grant()
            other_env = {**agent_git_env(checkout, other)}
            other_env = {k: v.replace(f"http.{URL}.", f"http.http://{HOST}/acme/other.git.") for k, v in other_env.items()}
            code, out = await _sh(
                sandbox, f"cd {repo} && git push http://{HOST}/acme/other.git HEAD:refs/heads/{BRANCH}", other_env
            )
            assert code != 0, out
            assert not any(p.startswith("/acme/other.git/git-receive-pack") for p in served.paths)
            # GT-02 / GT-03 hold under a grant too: main and a force-push are refused, each with a fresh approval.
            main = await grant()
            code, out = await _sh(
                sandbox, f"cd {repo} && git push origin HEAD:refs/heads/main", agent_git_env(checkout, main)
            )
            assert code != 0, out
            forced = await grant()
            code, out = await _sh(
                # The pushed commit rewritten: not a fast-forward of the session branch.
                sandbox,
                f"cd {repo} && git reset -q --hard {first} && git {IDENTITY} commit -q --amend -m c "
                f"&& git push -f origin HEAD:refs/heads/{BRANCH}",
                agent_git_env(checkout, forced),
            )
            assert code != 0, out
            assert git("rev-parse", f"refs/heads/{BRANCH}", cwd=bare) == first
            assert git("rev-parse", "main", cwd=bare) == head
        finally:
            await control.aclose()
    finally:
        await broker.release(sandbox)
