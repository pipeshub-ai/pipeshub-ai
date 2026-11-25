"""GITF-D1..GITF-D6 on the real lane (runc, WP-63 part A): a public repository fetched by the worker outside the sandbox
and handed to an engine image's sandbox under ``DockerSandboxBroker``, behind the real egress proxy.

A ``pxh-`` https git host container (the release opencode image's own git, smart HTTP over ``git upload-pack`` behind TLS on 443 with
a test authority) on the rig bridge stands for github.com; nothing leaves the host. Its address is private on the rig,
so the deployment opens its one name with ``EGRESS_PRIVATE_ALLOW`` as an internal mirror would; a second name for the
same address is not opened and stays refused. The fetch is the production ``RepositoryFetcher`` (the host's pinned
git, the hardened config, the address pinned, no redirects) and ``RepositoryFetchTools`` (the admin's hosts, the
egress cap, the journal event); the hand-off is the broker's ``seed_tar`` into the sandbox's read-only session folder,
and the checkout is this worker's ``ph.py`` run in the sandbox. Shown:

- GITF-D1: the clone works after approval and the sandbox's checkout runs nothing of the repository (no hook, no
  filter, no submodule fetched over ``file://``); the tool's event and the run's egress count carry its size;
- GITF-D2: a denied ask fetches nothing (the host saw no request); a grant answers a later fetch of that repository
  only, another repository asks again;
- GITF-D3: an oversized pack and a redirect to the metadata address are refused and nothing is kept; a name that
  points at the same private address without the deployment's word is never dialled;
- GITF-D4: the sandbox reaches the git host neither directly nor as an allowed website (git's smart-HTTP refused);
- GITF-D5: the cache stays under its bound and evicts the least recently used fetch.
"""

from __future__ import annotations

import contextlib
import json
import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

from app.harness.repositories import (
    FETCHED_EVENT,
    REPOSITORY_FETCH,
    RepositoryAsks,
    RepositoryFetchTools,
    file_chunks,
    repository_destination,
    repository_effect,
)
from harness_core.clock import SystemClock
from harness_core.effects.flow import ConversationGrants
from harness_core.effects.types import ToolContext
from harness_core.engines.controller import engine_mode
from harness_core.engines.sites import install_site_trust, site_env
from harness_core.journal.types import JournalEvent
from harness_core.kernel.core.messages import ToolCall
from harness_core.repos.fetch import MIB, FetchConfig, RepositoryFetcher
from harness_core.repos.location import RepositoryLocation, RepositoryRefused
from harness_core.sandbox.egress.errors import AddressRefused
from harness_core.sandbox.egress.upstream_proxy import parse_private_allow
from harness_core.sandbox.egress.volume import EgressVolume, VolumeLimits
from harness_core.sandbox.tar import config_tar
from harness_core.sandbox.types import (
    EgressDestination,
    EgressPolicy,
    LeaseRef,
    MountSpec,
    SandboxSpec,
    SiteAccess,
    SiteMode,
)
from harness_core.workspace.sync.archive import Owner
from tests.support.git_host import bare_repository, commit, git, https_pki, working_copy
from tests.unit.harness.engines.real_stack import (
    IMAGE,
    OWNER,
    engine_broker,
    lane_available,
    start_stack,
    stop_stack,
)
from tests.unit.harness.sandbox_broker.conftest import cleanup, cleanup_networks

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from app.harness.sandbox.docker_broker import DockerSandboxBroker
    from harness_core.sandbox.ports import SandboxHandle
    from tests.unit.harness.engines.real_stack import RealStack

pytestmark = [pytest.mark.docker, pytest.mark.timeout(900, method="thread")]

HOST = "githost.pxh-gitf.dev"
UNLISTED = "mirror.pxh-gitf.dev"
SESSION = "/pipeshub/session"
REPOS = "/pipeshub/repos"
WORKSPACE = "/workspace"
PH = Path(__file__).resolve().parents[4] / "harness_engines" / "pipeshub" / "ph.py"
GIT_SERVER = r"""
import gzip, http.server, os, socketserver, ssl, subprocess
open('/tmp/c.pem', 'w').write(os.environ['SITE_CERT']); open('/tmp/k.pem', 'w').write(os.environ['SITE_KEY'])
# The image's git has no http-backend: git's smart HTTP is upload-pack over stateless RPC, served here.
ENV = {'PATH': '/usr/bin:/bin:/usr/local/bin', 'HOME': '/tmp', 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
       'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'safe.directory', 'GIT_CONFIG_VALUE_0': '*'}
def pkt(text):
    return ('%04x' % (len(text) + 4)).encode() + text.encode()
class H(http.server.BaseHTTPRequestHandler):
    def _answer(self, status, kind, payload):
        self.send_response(status)
        if kind:
            self.send_header('Content-Type', kind); self.send_header('Cache-Control', 'no-cache')
        self.send_header('Content-Length', str(len(payload))); self.end_headers(); self.wfile.write(payload)
    def _git(self):
        with open('/tmp/requests.log', 'a') as log:
            log.write(self.path + '\n')
        if self.path.startswith('/acme/moved'):
            self.send_response(302); self.send_header('Location', 'https://169.254.169.254/latest/meta-data/')
            self.send_header('Content-Length', '0'); self.end_headers(); return
        path, _, query = self.path.partition('?')
        repo, _, service = path.rpartition('.git/')
        directory = '/srv' + repo + '.git'
        if '..' in path or not os.path.isdir(directory):
            return self._answer(404, None, b'')
        env = dict(ENV)
        if self.headers.get('Git-Protocol'):
            env['GIT_PROTOCOL'] = self.headers['Git-Protocol']
        if self.command == 'GET' and service == 'info/refs' and query == 'service=git-upload-pack':
            out = subprocess.run(['git', 'upload-pack', '--stateless-rpc', '--http-backend-info-refs', directory],
                                 env=env, capture_output=True).stdout
            head = b'' if 'version=2' in env.get('GIT_PROTOCOL', '') else pkt('# service=git-upload-pack\n') + b'0000'
            return self._answer(200, 'application/x-git-upload-pack-advertisement', head + out)
        if self.command == 'POST' and service == 'git-upload-pack':
            body = self.rfile.read(int(self.headers.get('Content-Length') or 0))
            if self.headers.get('Content-Encoding') == 'gzip':
                body = gzip.decompress(body)
            out = subprocess.run(['git', 'upload-pack', '--stateless-rpc', directory], input=body, env=env,
                                 capture_output=True).stdout
            return self._answer(200, 'application/x-git-upload-pack-result', out)
        return self._answer(403, None, b'')
    do_GET = do_POST = _git
    def log_message(self, *a): pass
class S(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
server = S(('0.0.0.0', 443), H)
context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH); context.load_cert_chain('/tmp/c.pem', '/tmp/k.pem')
server.socket = context.wrap_socket(server.socket, server_side=True)
server.serve_forever()
"""
CHECKOUT = r"""
import os, subprocess, sys
sys.path.insert(0, '/pipeshub/session')
import ph
ph.checkout_clone(sys.argv[1], sys.argv[2], sys.argv[3], None, full=False)
print(sorted(os.listdir(sys.argv[2])))
print(subprocess.run(['git', '-C', sys.argv[2], 'log', '--format=%H', '-1'], capture_output=True, text=True).stdout.strip())
print(os.path.exists('/tmp/pxh-gitf-pwned'))
sub = os.path.join(sys.argv[2], 'sub')
print(os.path.isdir(sub) and bool(os.listdir(sub)))
"""



@pytest.fixture(autouse=True)
def roomy_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    """The disk guard reads the cache's volume (GITF-34 sets its own): these tests run on whatever host they get.
    A roomy volume in real sizes: GITF-38's reserve is at least 2 GB, so a volume measured in bytes is always low."""
    from collections import namedtuple

    usage = namedtuple("usage", "total used free")
    gib = 1024**3
    monkeypatch.setattr(
        "harness_core.repos.fetch.shutil.disk_usage", lambda _path: usage(1000 * gib, 100 * gib, 900 * gib)
    )


class _Rig:
    def __init__(self, stack: RealStack, address: str, ca: Path, server: str, srv: Path, seed: Path) -> None:
        self.stack, self.address, self.ca, self.server, self.srv, self.seed = stack, address, ca, server, srv, seed

    def requests(self) -> list[str]:
        """The paths the git host was asked for, in order."""
        result = self.stack.client.api.exec_create(self.server, ["sh", "-c", "cat /tmp/requests.log 2>/dev/null || true"])
        out = self.stack.client.api.exec_start(result["Id"])
        return [line for line in out.decode().splitlines() if line]


class _Resolver:
    """The rig has no DNS: both names answer the git host's private address."""

    def __init__(self, address: str) -> None:
        self.address = address

    async def resolve(self, host: str) -> tuple[str, ...]:
        if host not in (HOST, UNLISTED):
            raise AddressRefused(host)
        return (self.address,)


@pytest.fixture(scope="module")
def rig(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_Rig]:
    available, reason = lane_available()
    if not available:
        pytest.skip(reason)
    root = tmp_path_factory.mktemp("gitf")
    seed = working_copy(root / "seed")
    commit(seed, "README.md", "hello from the rig\n")
    # A hostile repository's own files: a filter its attributes name, a submodule over file://, a hook-looking file.
    commit(seed, ".gitattributes", "* filter=evil\n")
    commit(seed, ".gitmodules", '[submodule "x"]\n\tpath = sub\n\turl = file:///etc\n')
    commit(seed, "hooks/post-checkout", "#!/bin/sh\ntouch /tmp/pxh-gitf-pwned\n")
    srv = root / "srv"
    _ = bare_repository(srv, "acme/widgets", seed)
    other = working_copy(root / "other")
    commit(other, "OTHER.md", "other\n")
    _ = bare_repository(srv, "acme/other", other)
    big = working_copy(root / "big")
    (big / "blob.bin").write_bytes(os.urandom(4 * MIB))
    git("add", "-A", cwd=big)
    git("commit", "-q", "-m", "big", cwd=big)
    _ = bare_repository(srv, "acme/huge", big)
    os.chmod(root, 0o755)
    for directory, _, files in os.walk(srv):
        os.chmod(directory, 0o755)
        for name in files:
            os.chmod(os.path.join(directory, name), 0o644)
    ca, cert, key = https_pki([HOST, UNLISTED])
    (root / "ca.pem").write_text(ca)
    server = f"pxh-gitf-host-{uuid.uuid4().hex[:8]}"
    holder: dict[str, str] = {}

    def upstreams(stack: RealStack) -> dict[str, str]:
        host_config = stack.client.api.create_host_config(
            network_mode=stack.network, binds={str(srv): {"bind": "/srv", "mode": "ro"}},
            cap_drop=["ALL"], cap_add=["NET_BIND_SERVICE", "SETUID", "SETGID"],
            security_opt=["no-new-privileges:true"], mem_limit=256 * 1024 * 1024,
        )
        created = stack.client.api.create_container(
            IMAGE, entrypoint=["python3", "-c", GIT_SERVER], user="0", name=server, labels=OWNER,
            environment={"SITE_CERT": cert, "SITE_KEY": key}, host_config=host_config,
        )
        stack.client.api.start(created["Id"])
        address = stack.client.api.inspect_container(server)["NetworkSettings"]["Networks"][stack.network]["IPAddress"]
        holder["address"] = address
        return {
            "EGRESS_TEST_INTERCEPT": "1",
            "EGRESS_TEST_UPSTREAM_CA": ca,
            "EGRESS_TEST_HOSTS": json.dumps({HOST: [address]}),
            "EGRESS_PRIVATE_ALLOW": f"{HOST}:443",
        }

    stack = start_stack(upstreams=upstreams)
    try:
        yield _Rig(stack, holder["address"], root / "ca.pem", server, srv, seed)
    finally:
        stop_stack(stack)
        with contextlib.suppress(Exception):
            stack.client.api.remove_container(server, force=True)


@pytest.fixture
async def broker(rig: _Rig) -> AsyncIterator[DockerSandboxBroker]:
    namespace = f"io.pipeshub.gitf.{uuid.uuid4().hex[:8]}"
    prefix = "pxh-gitf-" + namespace.rsplit(".", 1)[1]
    created, provider = await engine_broker(rig.stack, namespace, prefix)
    yield created
    await created.aclose()
    await provider.run_blocking(cleanup, provider, namespace, prefix)
    await provider.run_blocking(cleanup_networks, provider, namespace, rig.stack.proxy)
    provider.close()


def _fetcher(rig: _Rig, cache: Path, **config: Any) -> RepositoryFetcher:  # noqa: ANN401
    return RepositoryFetcher(
        FetchConfig(cache_dir=cache, git_bin="/usr/bin/git", ca_file=str(rig.ca), **config),
        _Resolver(rig.address),
        private_allow=parse_private_allow(f"{HOST}:443"),
    )


async def _sandbox(
    broker: DockerSandboxBroker, egress: EgressPolicy = EgressPolicy(), *, repos_mb: int = 32
) -> SandboxHandle:
    mode = engine_mode(broker.modes())
    assert mode is not None
    spec = SandboxSpec(
        image_digest=IMAGE, mode=mode, egress=egress, labels={"pxh.test": "gitf"},
        mounts=(MountSpec(WORKSPACE), MountSpec(SESSION, read_only=True), MountSpec(REPOS, read_only=True, size_mb=repos_mb)),
    )
    return await broker.acquire(spec, lease=LeaseRef("worker-1", 1), idempotency_key=f"gitf-{uuid.uuid4().hex[:8]}")


async def _sh(sandbox: SandboxHandle, argv: list[str], env: dict[str, str] | None = None) -> tuple[int, str, str]:
    process = await sandbox.exec(argv, env={"HOME": "/tmp", **(env or {})})
    out = b"".join([chunk async for chunk in process.stdout])
    err = b"".join([chunk async for chunk in process.stderr])
    return await process.wait(timeout_s=300), out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


class _SandboxShelf:
    """The turn's repository folder as the controller's endpoint hands off into it: its own hand-off, with what the
    folder can take (``repos_mb`` less the slack, as ``controller_config`` sizes it)."""

    def __init__(self, sandbox: SandboxHandle, repos_mb: int = 32) -> None:
        from harness_core.engines.controller import (
            _SavingEndpoint,  # pyright: ignore[reportPrivateUsage]
        )
        from harness_core.logs import HarnessLog
        from harness_core.repos.fetch import HAND_OFF_SLACK_MB

        self.endpoint = _SavingEndpoint(cast("Any", None))
        self.endpoint.shelf = (cast("Any", None), sandbox, HarnessLog.named("gitf"))
        self.endpoint.repos = REPOS
        self.endpoint.repos_left = max(0, repos_mb - HAND_OFF_SLACK_MB) * MIB if repos_mb > HAND_OFF_SLACK_MB else repos_mb * MIB // 2

    async def hand_off(self, archive: AsyncIterator[bytes], size_bytes: int) -> str | None:
        return await self.endpoint.hand_off(archive, size_bytes)


class _Volume:
    def __init__(self) -> None:
        self.volume = EgressVolume(VolumeLimits(max_run_bytes=256 * MIB), clock=SystemClock())

    async def charge(self, *, org_id: str, run_id: str, received: int) -> tuple[bool, int | None]:
        charged = self.volume.charge(org_id=org_id, run_id=run_id, received=received)
        return charged.allowed, charged.remaining


CTX = ToolContext(run_id="run-d", org_id="org-d", invocation_id="i1", attempt_id="a1", idempotency_key="k1")


def _call(path: str) -> ToolCall:
    return ToolCall(id="c1", name=REPOSITORY_FETCH, arguments={"url": f"https://{HOST}/{path}"})


async def _hosts() -> list[dict[str, Any]]:
    return [{"host": HOST, "prefixes": ["acme/"]}]


def _allow(call: ToolCall, effect: Any) -> str:  # noqa: ANN401
    return "allow"


class _Ledger:
    def apply(self, events: Any) -> None:  # noqa: ANN401
        del events

    def source_records(self, run_id: str) -> frozenset[str]:
        return frozenset()


def _grant(call: ToolCall) -> ConversationGrants:
    where = repository_destination(call, repository_effect())
    assert where is not None
    grants = ConversationGrants()
    envelope = {"orgId": "o", "sessionId": "s", "streamId": "s", "runId": "r", "schemaVersion": 1, "critical": False,
                "ts": "2026-11-01T12:00:00.000Z", "visibility": "owner"}
    grants.apply([
        JournalEvent.from_envelope({**envelope, "seq": 1, "writer": "worker", "type": "interaction.explained", "data": {
            "interactionId": "ix", "toolId": repository_effect().tool_id, "conversationGrant": True,
            "destination": {"label": where.label, "key": where.key}}}),
        JournalEvent.from_envelope({**envelope, "seq": 2, "writer": "node", "type": "interaction.resolved",
                                    "data": {"interactionId": "ix", "decision": "approved"}}),
        JournalEvent.from_envelope({**envelope, "seq": 3, "writer": "node", "type": "interaction.granted",
                                    "data": {"interactionId": "ix", "scope": "conversation"}}),
    ])
    return grants


async def test_gitf_d1_d2_d4_a_fetch_after_approval_is_handed_over_and_checked_out_inside_and_nothing_else_reaches_the_host(
    broker: DockerSandboxBroker, rig: _Rig, tmp_path: Path
) -> None:
    sandbox = await _sandbox(broker)
    try:
        await sandbox.seed_tar(SESSION, config_tar({"ph.py": PH.read_bytes()}))
        volume = _Volume()
        tools = RepositoryFetchTools(
            fetcher=_fetcher(rig, tmp_path / "cache"), hosts=_hosts, shelf=_SandboxShelf(sandbox), volume=volume,
            journal_events=True,
        )
        policy = RepositoryAsks(_allow, _Ledger(), "r", None, size_of=tools.size_of("org-d"), cap_bytes=500 * MIB)
        call, effect = _call("acme/widgets"), repository_effect()

        # GITF-D2: the person is asked; a denial runs nothing, so the host never hears of it.
        assert policy(call, effect) == "ask"
        reason = policy.ask_reason(call, effect)
        assert reason is not None and reason["destination"]["label"] == f"{HOST}/acme/widgets (public, read-only, up to 500 MB)"  # type: ignore[index]
        assert rig.requests() == []

        # GITF-D1: approved, the worker fetches and hands the bare copy to the sandbox, which checks it out itself.
        done = await tools.execute(call, CTX)
        assert done.status == "ok", done.content
        answer = json.loads(str(done.content))
        assert answer["path"].startswith(f"{REPOS}/") and answer["readOnly"] is True
        requests = rig.requests()
        assert requests and all(p.startswith("/acme/widgets.git/") for p in requests), requests
        # WP110-FU-1: the copy is the sandbox user's. The clone below cannot show it: on this image's git 2.54 a shallow
        # copy clones even when root owns it (seen on the base, where a full one is refused), and claude_code's 2.39 refuses both.
        code, owner, _ = await _sh(sandbox, ["stat", "-c", "%u:%g", answer["path"], f"{answer['path']}/HEAD"])
        assert code == 0 and set(owner.split()) == {f"{Owner().uid}:{Owner().gid}"}, owner
        code, out, err = await _sh(sandbox, ["python3", "-c", CHECKOUT, answer["path"], f"{WORKSPACE}/widgets", answer["url"]])
        assert code == 0, err
        listed, head, pwned, submodule = out.strip().splitlines()[-4:]
        assert "README.md" in listed and head == git("rev-parse", "HEAD", cwd=rig.seed)
        assert (pwned, submodule) == ("False", "False"), "no hook ran and no submodule was fetched"
        code, origin, _ = await _sh(sandbox, ["git", "-C", f"{WORKSPACE}/widgets", "remote", "get-url", "origin"])
        assert origin.strip() == f"https://{HOST}/acme/widgets"
        code, readme, _ = await _sh(sandbox, ["cat", f"{WORKSPACE}/widgets/README.md"])
        assert readme == "hello from the rig\n", "the filter the attributes name was never configured, so never ran"
        # The copy is read-only to the engine: it cannot plant anything for a later checkout.
        code, _, _ = await _sh(sandbox, ["sh", "-c", f"touch {answer['path']}/hooks-planted"])
        assert code != 0
        (event,) = done.events
        assert event.type == FETCHED_EVENT and event.data["bytes"] == answer["bytes"]
        moved = volume.volume.volume(org_id="org-d", run_id="run-d")
        assert moved is not None and moved.down == answer["bytes"]

        # GITF-D2: the grant answers this repository only; another one asks again.
        granted = RepositoryAsks(_allow, _Ledger(), "r", _grant(call), size_of=tools.size_of("org-d"), cap_bytes=500 * MIB)
        assert granted(call, effect) == "allow"
        assert granted(_call("acme/other"), effect) == "ask"
        again = granted.ask_reason(_call("acme/widgets"), effect)
        assert again is None
        # The card for a repository fetched before says about how large it is.
        second = policy.ask_reason(call, effect)
        assert second is not None and "about" in second["destination"]["label"]  # type: ignore[index]

        # GITF-D4: the sandbox itself never reaches the git host: not a destination, so the CONNECT is refused.
        code, _, err = await _sh(
            sandbox, ["git", "ls-remote", f"https://{HOST}/acme/widgets.git"], {**site_env(), "GIT_TERMINAL_PROMPT": "0"}
        )
        assert code != 0, err
    finally:
        await broker.release(sandbox)


async def test_gitf_d4_even_listed_as_a_website_the_git_host_refuses_git(broker: DockerSandboxBroker, rig: _Rig) -> None:
    before = len(rig.requests())
    egress = EgressPolicy(destinations=(EgressDestination(HOST, (443,), site=SiteAccess(SiteMode.READ)),))
    sandbox = await _sandbox(broker, egress)
    try:
        await install_site_trust(sandbox)
        code, _, err = await _sh(
            sandbox, ["git", "ls-remote", f"https://{HOST}/acme/widgets.git"], {**site_env(), "GIT_TERMINAL_PROMPT": "0"}
        )
        assert code != 0 and "403" in err, err
        assert len(rig.requests()) == before, "the proxy refused git's smart-HTTP before forwarding it"
    finally:
        await broker.release(sandbox)


async def test_gitf_d3_a_hostile_host_is_neutralised_and_d5_the_cache_stays_bounded(rig: _Rig, tmp_path: Path) -> None:
    small = _fetcher(rig, tmp_path / "small", repo_max_bytes=1 * MIB)
    with pytest.raises(RepositoryRefused) as huge:
        _ = await small.fetch("org-d", RepositoryLocation(HOST, "acme/huge"), ref=None, full=False)
    assert huge.value.reason == "repository_too_large"
    with pytest.raises(RepositoryRefused) as moved:
        _ = await small.fetch("org-d", RepositoryLocation(HOST, "acme/moved"), ref=None, full=False)
    assert moved.value.reason == "repository_redirect"
    assert small.cache.entries() == []
    calls = len(rig.requests())
    with pytest.raises(RepositoryRefused) as unlisted:
        _ = await small.fetch("org-d", RepositoryLocation(UNLISTED, "acme/widgets"), ref=None, full=False)
    assert unlisted.value.reason == "repository_address_refused"
    assert len(rig.requests()) == calls, "a private address the deployment did not open is never dialled"

    fetcher = _fetcher(rig, tmp_path / "bounded")
    widgets = await fetcher.fetch("org-d", RepositoryLocation(HOST, "acme/widgets"), ref=None, full=False)
    fetcher.cache.max_bytes = widgets.size_bytes + 1
    other = await fetcher.fetch("org-d", RepositoryLocation(HOST, "acme/other"), ref=None, full=False)
    kept = {key for key, _, _ in fetcher.cache.entries()}
    assert kept == {other.key} and fetcher.cache.total_bytes() <= fetcher.cache.max_bytes
    print(f"gitf cache: widgets {widgets.size_bytes} B, other {other.size_bytes} B, cap {fetcher.cache.max_bytes} B")


async def test_gitf_d1_the_tar_streams_in_chunks(rig: _Rig, tmp_path: Path) -> None:
    fetched = await _fetcher(rig, tmp_path / "c").fetch("org-d", RepositoryLocation(HOST, "acme/widgets"), ref=None, full=False)
    chunks = [chunk async for chunk in file_chunks(fetched.tar)]
    assert sum(len(c) for c in chunks) == fetched.size_bytes


async def test_gitf_d7_the_repository_folder_is_its_own_mount_sized_to_the_cap_and_a_repository_over_it_is_refused(
    broker: DockerSandboxBroker, rig: _Rig, tmp_path: Path
) -> None:
    sandbox = await _sandbox(broker, repos_mb=2)
    try:
        code, out, err = await _sh(sandbox, ["sh", "-c", f"df -m {REPOS} | tail -1; touch {REPOS}/x"])
        assert code != 0, "the repository folder is read-only to the engine"
        assert out.split()[1] in ("2", "3"), out
        tools = RepositoryFetchTools(
            fetcher=_fetcher(rig, tmp_path / "c", repo_max_bytes=8 * MIB), hosts=_hosts,
            shelf=_SandboxShelf(sandbox, repos_mb=2),
        )
        done = await tools.execute(_call("acme/huge"), CTX)
        assert done.status == "error" and "does not fit in this sandbox's repository folder" in str(done.content)
        # GITF-35: the hand-off was refused before anything was written: the folder holds nothing of it.
        code, listed, _ = await _sh(sandbox, ["sh", "-c", f"ls -A {REPOS}"])
        assert code == 0 and listed.strip() == "", listed
        # A repository that fits is handed over whole.
        small = await tools.execute(_call("acme/widgets"), CTX)
        assert small.status == "ok", small.content
        _, listed, _ = await _sh(sandbox, ["sh", "-c", f"ls -A {REPOS}"])
        assert listed.strip() == json.loads(str(small.content))["path"].rsplit("/", 1)[1]
    finally:
        await broker.release(sandbox)


LINKED_CLONE = r"""
import json, os, sys
sys.path.insert(0, '/pipeshub/session')
import ph
answer = {'path': sys.argv[1], 'url': sys.argv[2], 'label': 'acme/widgets', 'bytes': 1, 'commit': sys.argv[3]}

class Gateway:
    def tools(self):
        return [{'name': ph.REPOSITORY_FETCH, 'description': '', 'inputSchema': {}}]

    def call(self, name, arguments, **_):
        return {'content': [{'type': 'text', 'text': json.dumps(answer)}], 'isError': False}

os.chdir('/workspace')
done = ph.git_clone(Gateway(), sys.argv[2], 'repos/widgets', None, full=False)
print(done.code, os.path.islink('repos/widgets'), os.readlink('repos/widgets'), os.path.isfile('repos/widgets/README.md'))
"""


async def test_gitf_d8_wp98_fu1_a_clone_lives_on_the_references_mount_and_a_save_holds_only_its_link(
    broker: DockerSandboxBroker, rig: _Rig, tmp_path: Path
) -> None:
    """WP98-FU-1 (ADR-RUN-02): the clone, then the link, then a save that streams and stores no file of the checkout,
    then a refresh that keeps the link."""
    import dataclasses

    from harness_core.engines.controller import references_mount_for
    from harness_core.workspace import ObjectScope
    from harness_core.workspace.memory import (
        InMemoryBinaryObjectStore,
        InMemoryWorkspaceStore,
    )
    from harness_core.workspace.sync import SyncLimits, TarWorkspaceSync, WorkspaceSaver
    from harness_core.workspace.sync.storage_classes import (
        REFERENCES_DIR,
        reference_identity,
    )
    from harness_core.workspace.sync.types import REFERENCE_COPY

    mode = engine_mode(broker.modes())
    assert mode is not None
    spec = SandboxSpec(
        image_digest=IMAGE, mode=mode, labels={"pxh.test": "gitf"},
        mounts=(
            MountSpec(WORKSPACE), MountSpec(SESSION, read_only=True), MountSpec(REPOS, read_only=True, size_mb=32),
            references_mount_for(MountSpec(WORKSPACE), REFERENCES_DIR, 64),
        ),
    )
    sandbox = await broker.acquire(spec, lease=LeaseRef("worker-1", 1), idempotency_key=f"gitf-{uuid.uuid4().hex[:8]}")
    try:
        await sandbox.seed_tar(SESSION, config_tar({"ph.py": PH.read_bytes()}))
        tools = RepositoryFetchTools(fetcher=_fetcher(rig, tmp_path / "cache"), hosts=_hosts, shelf=_SandboxShelf(sandbox))
        done = await tools.execute(_call("acme/widgets"), CTX)
        assert done.status == "ok", done.content
        answer = json.loads(str(done.content))
        code, out, err = await _sh(sandbox, ["python3", "-c", LINKED_CLONE, answer["path"], answer["url"], answer["commit"]])
        assert code == 0, err
        result, is_link, target, readme = out.strip().splitlines()[-1].split()
        assert (result, is_link, readme) == ("0", "True", "True") and target.startswith(f"{REFERENCES_DIR}/"), out

        scope = ObjectScope("org-d", "ses-d")
        clock = SystemClock()
        sync = TarWorkspaceSync(objects=InMemoryBinaryObjectStore(), scope=scope, clock=clock, limits=SyncLimits(max_entries=50))
        store = InMemoryWorkspaceStore(clock=clock)
        manifest = await store.head(ObjectScope("org-d"), "ws:ses-d")
        state = await sync.materialize(sandbox, manifest, root=WORKSPACE, keep_scratch=True)
        vouched = dataclasses.replace(state, vouched=frozenset({reference_identity(answer["url"])}))
        collected = await sync.collect(sandbox, vouched)
        assert collected.skipped.get("repos/widgets") == REFERENCE_COPY, collected.skipped
        assert not any(p.startswith("repos/widgets/") for p in collected.changes.upserts)
        assert [r.version for r in collected.references] == [answer["commit"]]
        saved = await WorkspaceSaver(sync=sync, store=store, scope=ObjectScope("org-d")).save(sandbox, vouched, reason="turn_end")
        assert saved.state.references and saved.state.manifest is not None
        assert not any(p.startswith("repos/") for p in saved.state.manifest.files)
        await sync.materialize(sandbox, saved.state.manifest, root=WORKSPACE, keep_scratch=True)
        code, kept, _ = await _sh(sandbox, ["sh", "-c", f"readlink {WORKSPACE}/repos/widgets && cat {WORKSPACE}/repos/widgets/README.md"])
        assert code == 0 and kept.splitlines()[0] == target, kept
    finally:
        await broker.release(sandbox)
