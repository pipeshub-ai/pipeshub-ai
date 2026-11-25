"""GITF-01..GITF-12 (WP-63 part A): ``repository_fetch``, a public repository cloned by the worker after the person
allows it, handed to the sandbox read-only.

Unit lane: the URL rules, the admin's hosts and an agent's narrowing, the SSRF check, the hardened git command, a real
fetch from a local https ``git http-backend`` (the address check stubbed to its loopback address, which the real check
always refuses: GITF-03), the cache's bound, the caps, the approval layer and its per-repository conversation grant,
the tool's refusals and its journal event, the egress cap, and ``ph git clone`` / ``ph git fetch`` checking the copy out
with hooks, filters and submodules off.
"""

from __future__ import annotations

import io
import json
import logging
import os
import ssl
import tarfile
from typing import TYPE_CHECKING, Any, cast

import pytest

from app import edition_config
from app.harness.repositories import (
    ASK_LAYER,
    ASK_RULE,
    FETCHED_EVENT,
    REPOSITORY_FETCH,
    TOOL_ID,
    RepositoryAsks,
    RepositoryCatalog,
    RepositoryFetchTools,
    capped_hosts,
    card_label,
    fetch_config_from_env,
    live_repository_hosts,
    prefix_within,
    repository_destination,
    repository_effect,
    repository_key,
)
from harness_core.clock import SystemClock
from harness_core.effects.flow import (
    ConversationGrants,
    FlowGuardedPolicy,
    FlowRule,
    sends_out,
)
from harness_core.effects.types import ToolContext
from harness_core.journal.types import JournalEvent
from harness_core.kernel.core.messages import ToolCall
from harness_core.repos.cache import RepositoryCache
from harness_core.repos.fetch import (
    MIB,
    FetchConfig,
    RepositoryFetcher,
    clone_argv,
    failure_of,
    git_env,
)
from harness_core.repos.location import (
    RepositoryLocation,
    RepositoryRefused,
    allowed_entry,
    check_ref,
    parse_repository_url,
)
from harness_core.sandbox.egress.errors import AddressRefused
from harness_core.sandbox.egress.policy import parse_networks
from harness_core.sandbox.egress.upstream_proxy import parse_private_allow
from harness_core.sandbox.egress.volume import EgressVolume, VolumeLimits
from harness_engines.pipeshub import ph
from tests.support.git_host import (
    GitHttpHost,
    bare_repository,
    commit,
    git,
    https_pki,
    working_copy,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping, Sequence
    from pathlib import Path

HOST = "git.pxh-gitf.dev"
LOCATION = RepositoryLocation(HOST, "acme/widgets")



@pytest.fixture(autouse=True)
def roomy_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    """The disk guard reads the cache's volume (GITF-34 sets its own): these tests run on whatever host they get."""
    from collections import namedtuple

    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr("harness_core.repos.fetch.shutil.disk_usage", lambda _path: usage(1024**4, 100 * 1024**3, 924 * 1024**3))


# --- GITF-01: the URL -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/acme/widgets",
        "git@github.com:acme/widgets.git",
        "ssh://git@github.com/acme/widgets",
        "file:///etc/passwd",
        "ext::sh -c id",
        "https://user:pass@github.com/acme/widgets",
        "https://token@github.com/acme/widgets",
        "https://github.com:8443/acme/widgets",
        "https://140.82.112.3/acme/widgets",
        "https://[::1]/acme/widgets",
        "https://localhost/acme/widgets",
        "https://github.com/acme",
        "https://github.com/acme/widgets?x=1",
        "https://github.com/acme/widgets#frag",
        "https://github.com/acme/../etc",
        "https://github.com/acme/%2e%2e",
        "https://github.com/acme/widgets\nX: y",
        "",
    ],
)
def test_gitf01_only_an_https_url_naming_one_repository_by_host_name_is_read(url: str) -> None:
    with pytest.raises(RepositoryRefused) as refused:
        _ = parse_repository_url(url)
    assert refused.value.reason == "repository_url_invalid"


def test_gitf01_a_plain_url_names_its_host_and_path_and_refs_are_branch_names() -> None:
    found = parse_repository_url("https://GitHub.com/onyx-dot-app/onyx.git/")
    assert (found.host, found.path, found.label) == ("github.com", "onyx-dot-app/onyx", "github.com/onyx-dot-app/onyx")
    assert found.url == "https://github.com/onyx-dot-app/onyx.git"
    assert check_ref("main") == "main" and check_ref(None) is None
    for bad in ("--upload-pack=x", "../x", "a..b", "HEAD", 3):
        with pytest.raises(RepositoryRefused):
            _ = check_ref(bad)


# --- GITF-02: which hosts -------------------------------------------------------------------------------------------


def test_gitf02_hosts_and_prefixes_decide(monkeypatch: pytest.MonkeyPatch) -> None:
    hosts: list[Mapping[str, Any]] = [{"host": "github.com", "prefixes": ["onyx-dot-app/"]}, {"host": "git.example.com"}]
    onyx = parse_repository_url("https://github.com/onyx-dot-app/onyx")
    other = parse_repository_url("https://github.com/someone/else")
    lookalike = parse_repository_url("https://github.com/onyx-dot-app-evil/x")
    assert allowed_entry(onyx, hosts) is not None
    assert allowed_entry(other, hosts) is None and allowed_entry(lookalike, hosts) is None
    assert allowed_entry(parse_repository_url("https://git.example.com/a/b"), hosts) is not None
    # One repository prefix holds only that repository.
    one = [{"host": "github.com", "prefixes": ["onyx-dot-app/onyx"]}]
    assert allowed_entry(onyx, one) is not None
    assert allowed_entry(parse_repository_url("https://github.com/onyx-dot-app/onyx-other"), one) is None
    assert prefix_within("onyx-dot-app/onyx", "onyx-dot-app/") and not prefix_within("onyx-dot-app/", "onyx-dot-app/onyx")
    # Without an edition's layers an org's or an agent's list in the live layer changes nothing.
    monkeypatch.setattr(edition_config, "org_network_layers", None, raising=False)
    member = {"org": {"hosts": [{"host": "git.example.com"}]}, "agents": {"a1": {"hosts": []}}}
    assert live_repository_hosts(member, None, web=False) == []
    assert [h["host"] for h in live_repository_hosts(member, "a1", web=True)] == ["github.com", "gitlab.com", "bitbucket.org"]


# --- GITF-03: where the fetch may go --------------------------------------------------------------------------------


class _Resolver:
    def __init__(self, answers: dict[str, tuple[str, ...]]) -> None:
        self.answers = answers

    async def resolve(self, host: str) -> tuple[str, ...]:
        if host not in self.answers:
            raise AddressRefused(host)
        return self.answers[host]


def _fetcher(tmp_path: Path, resolver: _Resolver) -> RepositoryFetcher:
    return RepositoryFetcher(FetchConfig(cache_dir=tmp_path / "cache"), resolver)


@pytest.mark.parametrize(
    "answer",
    ["127.0.0.1", "10.0.0.5", "169.254.169.254", "192.168.1.1", "::1", "fd00::1", "100.64.0.1", "0.0.0.0"],
)
async def test_gitf03_a_host_that_points_inside_is_never_fetched(tmp_path: Path, answer: str) -> None:
    fetcher = _fetcher(tmp_path, _Resolver({HOST: ("140.82.112.3", answer)}))
    with pytest.raises(RepositoryRefused) as refused:
        _ = await fetcher.checked_address(HOST)
    assert refused.value.reason == "repository_address_refused"


async def test_gitf03_a_public_answer_is_pinned_and_the_deployment_may_open_a_private_mirror(tmp_path: Path) -> None:
    assert await _fetcher(tmp_path, _Resolver({HOST: ("140.82.112.3",)})).checked_address(HOST) == "140.82.112.3"
    mirror = RepositoryFetcher(
        FetchConfig(cache_dir=tmp_path / "c2"),
        _Resolver({HOST: ("10.20.0.7",), "other.pxh-gitf.dev": ("10.20.0.8",)}),
        private_allow=parse_private_allow(f"{HOST}:443"),
    )
    assert await mirror.checked_address(HOST) == "10.20.0.7"
    with pytest.raises(RepositoryRefused):
        _ = await mirror.checked_address("other.pxh-gitf.dev")
    # A blocked range (PipesHub's own networks) and the metadata address stay refused even under a private entry.
    blocked = RepositoryFetcher(
        FetchConfig(cache_dir=tmp_path / "c3"),
        _Resolver({HOST: ("10.20.0.7",)}),
        private_allow=parse_private_allow(f"{HOST}:443"),
        refused_networks=parse_networks(["10.20.0.0/16"]),
    )
    with pytest.raises(RepositoryRefused):
        _ = await blocked.checked_address(HOST)
    with pytest.raises(RepositoryRefused):
        _ = await _fetcher(tmp_path, _Resolver({})).checked_address(HOST)


# --- GITF-04: the hardened command ----------------------------------------------------------------------------------


def test_gitf04_git_runs_hardened_pinned_to_the_checked_address_and_with_nothing_of_the_worker(tmp_path: Path) -> None:
    config = FetchConfig(cache_dir=tmp_path, git_bin="/usr/bin/git", ca_file="/etc/pxh/ca.pem")
    argv = clone_argv(config, LOCATION, "140.82.112.3", tmp_path / "repo.git", ref="main", full=False)
    pairs = {argv[i + 1] for i, a in enumerate(argv) if a == "-c"}
    for wanted in (
        "protocol.allow=never", "protocol.https.allow=always", "protocol.file.allow=never", "http.followRedirects=false",
        "core.hooksPath=/dev/null", "credential.helper=", "submodule.recurse=false", "transfer.fsckObjects=true",
        "fetch.fsckObjects=true", "http.proxy=", f"http.curloptResolve={HOST}:443:140.82.112.3",
        "http.sslCAInfo=/etc/pxh/ca.pem", "filter.lfs.smudge=", "filter.lfs.process=",
    ):
        assert wanted in pairs, wanted
    assert argv[0] == "/usr/bin/git"
    tail = argv[argv.index("clone"):]
    assert tail[:2] == ["clone", "--bare"] and "--template=" in tail and "--no-recurse-submodules" in tail
    assert tail[tail.index("--depth") + 1] == "1" and tail[tail.index("--branch") + 1] == "main"
    assert tail[-3:] == ["--", f"https://{HOST}/acme/widgets.git", str(tmp_path / "repo.git")]
    assert "--depth" not in clone_argv(config, LOCATION, "140.82.112.3", tmp_path / "r", ref=None, full=True)
    v6 = clone_argv(config, LOCATION, "2606:50c0::153", tmp_path / "r", ref=None, full=True)
    assert f"http.curloptResolve={HOST}:443:[2606:50c0::153]" in v6
    env = git_env(tmp_path)
    assert env["GIT_CONFIG_NOSYSTEM"] == "1" and env["GIT_CONFIG_GLOBAL"] == "/dev/null"
    assert env["HOME"] == env["XDG_CONFIG_HOME"] == str(tmp_path) and env["GIT_TERMINAL_PROMPT"] == "0"
    assert not {k for k in env if "proxy" in k.lower() or "TOKEN" in k or "SSH_AUTH" in k}


def test_gitf04_git_failures_are_plain_refusals() -> None:
    assert failure_of("fatal: unable to access 'x': The requested URL returned error: 302").reason == "repository_redirect"
    assert failure_of("fatal: unable to access 'x': The requested URL returned error: 404").reason == "repository_not_found"
    assert failure_of("fatal: could not read Username for 'https://x': terminal prompts disabled").reason == "repository_not_found"
    assert failure_of("error: object abc: badTimezone\nfatal: fsck error in packed object").reason == "repository_corrupt"
    assert failure_of("something else").reason == "repository_fetch_failed"


def test_gitf04_the_deployment_settings_have_laptop_defaults_and_read_the_environment(tmp_path: Path) -> None:
    default = fetch_config_from_env({})
    assert (default.cache_max_bytes, default.repo_max_bytes, default.concurrency) == (2048 * MIB, 500 * MIB, 2)
    assert str(default.cache_dir) == "/data/pipeshub/harness/repository-cache"
    tuned = fetch_config_from_env({
        "HARNESS_REPO_CACHE_DIR": str(tmp_path), "HARNESS_REPO_CACHE_MAX_MB": "64", "HARNESS_REPO_MAX_MB": "8",
        "HARNESS_REPO_FETCH_CONCURRENCY": "1", "HARNESS_REPO_FETCH_TIMEOUT_S": "30", "HARNESS_REPO_CACHE_MAX_AGE_S": "0",
        "HARNESS_REPO_GIT_BIN": "/opt/git/bin/git", "HARNESS_REPO_CA_FILE": "/ca.pem",
    })
    assert tuned.cache_dir == tmp_path and tuned.cache_max_bytes == 64 * MIB and tuned.repo_max_bytes == 8 * MIB
    assert (tuned.concurrency, tuned.timeout_s, tuned.max_age_s, tuned.git_bin, tuned.ca_file) == (1, 30, 0, "/opt/git/bin/git", "/ca.pem")
    assert fetch_config_from_env({"HARNESS_REPO_MAX_MB": "lots"}).repo_max_bytes == 500 * MIB


# --- GITF-05..GITF-07: a real fetch from a local https host ---------------------------------------------------------


class _LoopbackFetcher(RepositoryFetcher):
    """The fetch as production runs it, but dialling the test host's loopback address (which the real check refuses,
    GITF-03)."""

    async def checked_address(self, host: str) -> str:
        assert host == HOST
        return "127.0.0.1"


@pytest.fixture
async def https_host(tmp_path: Path) -> AsyncIterator[tuple[GitHttpHost, Path, str, int]]:
    seed = working_copy(tmp_path / "seed")
    commit(seed, "README.md", "hello\n")
    # A hostile repository's own files: a filter the checkout would run if anything configured it, a submodule on
    # file:// and a hook-looking file. None of them may act anywhere.
    commit(seed, ".gitattributes", "* filter=evil\n")
    commit(seed, ".gitmodules", '[submodule "x"]\n\tpath = sub\n\turl = file:///etc\n')
    commit(seed, "hooks/post-checkout", "#!/bin/sh\ntouch /tmp/pxh-gitf-pwned\n")
    _ = bare_repository(tmp_path / "srv", "acme/widgets", seed)
    big = working_copy(tmp_path / "big")
    (big / "blob.bin").write_bytes(os.urandom(3 * MIB))
    git("add", "-A", cwd=big)
    git("commit", "-q", "-m", "big", cwd=big)
    _ = bare_repository(tmp_path / "srv", "acme/huge", big)
    ca, cert, key = https_pki([HOST])
    (tmp_path / "ca.pem").write_text(ca)
    (tmp_path / "cert.pem").write_text(cert)
    (tmp_path / "key.pem").write_text(key)
    tls = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    tls.load_cert_chain(str(tmp_path / "cert.pem"), str(tmp_path / "key.pem"))
    served = GitHttpHost(tmp_path / "srv", redirects={"/acme/moved": "https://10.0.0.1/acme/widgets.git"})
    _ = await served.start("127.0.0.1", tls=tls)
    yield served, seed, str(tmp_path / "ca.pem"), served.port
    await served.stop()


def _local(tmp_path: Path, port: int, ca: str, **config: Any) -> RepositoryFetcher:  # noqa: ANN401
    return _LoopbackFetcher(
        FetchConfig(cache_dir=tmp_path / "cache", git_bin="git", port=port, ca_file=ca, **config), _Resolver({})
    )


def _extract(tar: Path, into: Path) -> Path:
    with tarfile.open(tar) as archive:
        members = archive.getmembers()
        assert all(m.isdir() or (m.isfile() and m.mode == 0o444) for m in members)
        # One repository folder at the archive's root: the sandbox's repository mount holds it as it is.
        tops = {m.name.split("/")[0] for m in members}
        assert len(tops) == 1 and all(".." not in m.name.split("/") for m in members)
        archive.extractall(into, filter="data")
    return into / tops.pop()


async def test_gitf05_a_shallow_bare_fetch_is_handed_over_and_checked_out_with_nothing_of_the_repository_acting(
    https_host: tuple[GitHttpHost, Path, str, int], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    served, seed, ca, port = https_host
    fetcher = _local(tmp_path, port, ca)
    fetched = await fetcher.fetch("org-1", LOCATION, ref=None, full=False)
    assert not fetched.cached and fetched.head == git("rev-parse", "HEAD", cwd=seed)
    assert all(a is None for a in served.authorizations), "no credential ever reaches the host"
    bare = _extract(fetched.tar, tmp_path / "sandbox")
    assert (bare / "shallow").is_file(), "the default is the latest commit only"
    assert not (bare / "hooks").exists(), "a bare clone with no template holds no hooks"
    # The sandbox's side: ph clones the copy with hooks, filters and submodules off.
    work = tmp_path / "workspace"
    work.mkdir()
    monkeypatch.chdir(work)
    ph.checkout_clone(str(bare), "widgets", fetched.location.shown_url, None, full=False)
    assert (work / "widgets" / "README.md").read_text() == "hello\n"
    assert not (work / "widgets" / "sub").exists() or not os.listdir(work / "widgets" / "sub")
    assert not os.path.exists("/tmp/pxh-gitf-pwned")
    assert git("remote", "get-url", "origin", cwd=work / "widgets") == f"https://{HOST}/acme/widgets"
    # The same repository again within the reuse window moves nothing.
    again = await fetcher.fetch("org-1", LOCATION, ref=None, full=False)
    assert again.cached
    before = len(served.paths)
    _ = await fetcher.fetch("org-1", LOCATION, ref=None, full=False)
    assert len(served.paths) == before
    # Another org never reads this one's entry.
    other = await fetcher.fetch("org-2", LOCATION, ref=None, full=False)
    assert not other.cached and other.key != fetched.key


async def test_gitf05_ph_git_fetch_brings_the_new_commit_into_origin(
    https_host: tuple[GitHttpHost, Path, str, int], tmp_path: Path
) -> None:
    _, seed, ca, port = https_host
    fetcher = _local(tmp_path, port, ca, max_age_s=0)
    first = await fetcher.fetch("org-1", LOCATION, ref=None, full=False)
    work = tmp_path / "w"
    work.mkdir()
    ph.checkout_clone(str(_extract(first.tar, tmp_path / "s1")), str(work / "widgets"), LOCATION.shown_url, None, full=False)
    head = commit(seed, "NEWS.md", "news\n")
    git("push", "-q", str(tmp_path / "srv" / "acme" / "widgets.git"), "main", cwd=seed)
    second = await fetcher.fetch("org-1", LOCATION, ref=None, full=False)
    assert not second.cached and second.head == head
    ph.checkout_fetch(str(_extract(second.tar, tmp_path / "s2")), str(work / "widgets"))
    assert git("rev-parse", "origin/main", cwd=work / "widgets") == head


async def test_gitf06_an_oversized_pack_and_a_redirect_are_refused_and_nothing_is_kept(
    https_host: tuple[GitHttpHost, Path, str, int], tmp_path: Path
) -> None:
    _, _, ca, port = https_host
    fetcher = _local(tmp_path, port, ca, repo_max_bytes=1 * MIB)
    with pytest.raises(RepositoryRefused) as refused:
        _ = await fetcher.fetch("org-1", RepositoryLocation(HOST, "acme/huge"), ref=None, full=False)
    assert refused.value.reason == "repository_too_large"
    with pytest.raises(RepositoryRefused) as moved:
        _ = await fetcher.fetch("org-1", RepositoryLocation(HOST, "acme/moved"), ref=None, full=False)
    assert moved.value.reason == "repository_redirect"
    with pytest.raises(RepositoryRefused) as missing:
        _ = await fetcher.fetch("org-1", RepositoryLocation(HOST, "acme/nothing"), ref=None, full=False)
    assert missing.value.reason == "repository_not_found"
    assert fetcher.cache.entries() == [] and not [p for p in (tmp_path / "cache").iterdir() if p.name.startswith(".tmp-")]


async def test_gitf06_a_host_that_hangs_is_cut_at_the_time_limit(tmp_path: Path) -> None:
    script = tmp_path / "slow-git"
    script.write_text('#!/bin/sh\nif [ "$1" = "--version" ]; then echo "git version 2.43.0"; exit 0; fi\nexec sleep 30\n')
    script.chmod(0o755)
    fetcher = _LoopbackFetcher(FetchConfig(cache_dir=tmp_path / "cache", git_bin=str(script), timeout_s=0.5), _Resolver({}))
    with pytest.raises(RepositoryRefused) as refused:
        _ = await fetcher.fetch("org-1", LOCATION, ref=None, full=False)
    assert refused.value.reason == "repository_timeout"
    old = tmp_path / "old-git"
    old.write_text('#!/bin/sh\necho "git version 2.30.1"\n')
    old.chmod(0o755)
    with pytest.raises(RepositoryRefused) as unavailable:
        _ = await _LoopbackFetcher(FetchConfig(cache_dir=tmp_path / "c2", git_bin=str(old)), _Resolver({})).fetch(
            "org-1", LOCATION, ref=None, full=False
        )
    assert unavailable.value.reason == "repository_unavailable"


def test_gitf07_the_cache_is_bounded_least_recently_used_first_and_never_drops_a_pinned_entry(tmp_path: Path) -> None:
    now = [1000.0]
    cache = RepositoryCache(tmp_path / "cache", max_bytes=3000, max_age_s=60, clock=lambda: now[0])

    def put(key: str, size: int) -> None:
        staged = cache.temporary()
        (staged / "repo.tar").write_bytes(b"x" * size)
        _ = cache.put(key, staged, head="a" * 40, ref=None, label=key)
        now[0] += 1

    put("a", 1000)
    put("b", 1000)
    put("c", 1000)
    assert cache.get("a") is not None
    now[0] += 1
    put("d", 1000)
    assert {k for k, _, _ in cache.entries()} == {"a", "c", "d"}, "b was the least recently used"
    with cache.pinned("a"):
        now[0] += 100
        put("e", 2500)
        assert "a" in {k for k, _, _ in cache.entries()}
    assert cache.total_bytes() <= 3000 + 1000
    # Too old to reuse, but its size still tells the card what to expect.
    assert cache.get("e") is not None
    now[0] += 61
    assert cache.get("e") is None and cache.known_size("e") == 2500
    # A crashed fetch's leftovers go at start.
    leftover = tmp_path / "cache" / ".tmp-crashed"
    leftover.mkdir()
    fresh = RepositoryCache(tmp_path / "cache", max_bytes=3000, max_age_s=60)
    fresh.prepare()
    assert not leftover.exists()


# --- GITF-08: the person decides, per repository --------------------------------------------------------------------


class _Ledger:
    def __init__(self, records: frozenset[str] | None = frozenset()) -> None:
        self.records = records

    def apply(self, events: Sequence[JournalEvent]) -> None:
        del events

    def source_records(self, run_id: str) -> frozenset[str] | None:
        return self.records


def _call(url: str, **more: Any) -> ToolCall:  # noqa: ANN401
    return ToolCall(id="c1", name=REPOSITORY_FETCH, arguments={"url": url, **more})


def _allow(call: ToolCall, effect: Any) -> str:  # noqa: ANN401
    return "allow"


def _granted(call: ToolCall, interaction: str = "ix-1") -> ConversationGrants:
    grants = ConversationGrants()
    where = repository_destination(call, repository_effect())
    assert where is not None
    grants.apply([
        _event(1, "interaction.explained", "worker", {
            "interactionId": interaction, "toolId": TOOL_ID, "conversationGrant": True,
            "destination": {"label": where.label, "key": where.key},
        }),
        _event(2, "interaction.resolved", "node", {"interactionId": interaction, "decision": "approved"}),
        _event(3, "interaction.granted", "node", {"interactionId": interaction, "scope": "conversation"}),
    ])
    return grants


def _event(seq: int, kind: str, writer: str, data: dict[str, Any]) -> JournalEvent:
    return JournalEvent.from_envelope({
        "orgId": "o", "sessionId": "s", "streamId": "s", "seq": seq, "runId": "r", "schemaVersion": 1, "critical": False,
        "ts": "2026-11-01T12:00:00.000Z", "visibility": "owner", "writer": writer, "type": kind, "data": data,
    })


def _asks(base: Any = _allow, *, grants: ConversationGrants | None = None, records: frozenset[str] | None = frozenset()) -> RepositoryAsks:  # noqa: ANN401
    return RepositoryAsks(base, _Ledger(records), "r", grants, size_of=lambda location, call: None, cap_bytes=500 * MIB)


def test_gitf08_every_clone_asks_with_the_repository_named_and_offers_this_conversation() -> None:
    call = _call("https://github.com/onyx-dot-app/onyx")
    effect = repository_effect()
    assert sends_out(effect), "the URL the model chose leaves the organization (FG2)"
    policy = _asks()
    assert policy(call, effect) == "ask"
    reason = policy.ask_reason(call, effect)
    assert reason is not None
    assert (reason["rule"], reason["layer"], reason["conversationGrant"], reason["sensitivity"]) == (ASK_RULE, ASK_LAYER, True, "public")
    destination = reason["destination"]
    assert isinstance(destination, dict)
    assert destination["label"] == "github.com/onyx-dot-app/onyx (public, read-only, up to 500 MB)"
    assert card_label(LOCATION, 42 * MIB, 500 * MIB) == f"{HOST}/acme/widgets (public, read-only, about 42 MB)"
    # Another tool is left to the base policy.
    other = ToolCall(id="c2", name="slack__send", arguments={})
    assert policy(other, effect.__class__(tool_id="slack@x/send", effect="write", idempotent=False, sink=effect.sink)) == "allow"


def test_gitf08_the_conversation_grant_holds_that_repository_only_and_a_refusal_stays_a_refusal() -> None:
    effect = repository_effect()
    onyx = _call("https://github.com/onyx-dot-app/onyx")
    grants = _granted(onyx)
    policy = _asks(grants=grants)
    assert policy(onyx, effect) == "allow"
    assert policy(_call("https://github.com/onyx-dot-app/onyx.git", ref="v1", full=True), effect) == "allow"
    assert policy(_call("https://github.com/onyx-dot-app/other"), effect) == "ask"
    assert policy(_call("https://github.com/someone/onyx"), effect) == "ask"
    assert repository_key(parse_repository_url("https://github.com/Onyx-Dot-App/Onyx")) == repository_key(
        parse_repository_url("https://github.com/onyx-dot-app/onyx")
    )

    def deny(call: ToolCall, effect: Any) -> str:  # noqa: ANN401
        return "deny"

    assert _asks(deny, grants=grants)(onyx, effect) == "deny"


def test_gitf08_after_a_private_read_the_card_says_so_through_the_flow_rule() -> None:
    effect = repository_effect()
    call = _call("https://github.com/onyx-dot-app/onyx")
    ledger = _Ledger(frozenset({"rec-1"}))
    from app.harness.flow_destination import pipeshub_destination

    flow = FlowGuardedPolicy(_allow, FlowRule("ask"), ledger, "r", destination=pipeshub_destination)
    policy = RepositoryAsks(flow, ledger, "r", None, size_of=lambda location, c: 42 * MIB, cap_bytes=500 * MIB)
    assert policy(call, effect) == "ask"
    reason = policy.ask_reason(call, effect)
    assert reason is not None and reason["rule"] == "flow_external_write" and reason["sensitivity"] == "private"
    destination = reason["destination"]
    assert isinstance(destination, dict) and destination["label"].endswith("(public, read-only, about 42 MB)")
    # The flow rule's destination is the repository too, so one grant answers both.
    where = pipeshub_destination(call, effect)
    assert where is not None and where.key == repository_key(parse_repository_url("https://github.com/onyx-dot-app/onyx"))
    flow_granted = FlowGuardedPolicy(_allow, FlowRule("ask"), ledger, "r", destination=pipeshub_destination, grants=_granted(call))
    assert RepositoryAsks(flow_granted, ledger, "r", _granted(call), size_of=lambda location, c: None, cap_bytes=1)(call, effect) == "allow"


# --- GITF-09: the tool ----------------------------------------------------------------------------------------------


class _Shelf:
    def __init__(self, folder: str | None = "/pipeshub/repos") -> None:
        self.folder = folder
        self.received: list[bytes] = []

    async def hand_off(self, archive: AsyncIterator[bytes], size_bytes: int) -> str | None:
        self.received.append(b"".join([chunk async for chunk in archive]))
        return self.folder


class _Volume:
    def __init__(self, cap: int | None) -> None:
        self.volume = EgressVolume(VolumeLimits(max_run_bytes=cap or 0), clock=SystemClock())

    async def charge(self, *, org_id: str, run_id: str, received: int) -> tuple[bool, int | None]:
        charged = self.volume.charge(org_id=org_id, run_id=run_id, received=received)
        return charged.allowed, charged.remaining


CTX = ToolContext(run_id="run-1", org_id="org-1", invocation_id="i1", attempt_id="a1", idempotency_key="k1")


def _tools(fetcher: RepositoryFetcher, shelf: _Shelf, *, hosts: list[dict[str, Any]] | None = None, **more: Any) -> RepositoryFetchTools:  # noqa: ANN401
    async def listed() -> list[dict[str, Any]]:
        return [{"host": HOST}] if hosts is None else hosts

    return RepositoryFetchTools(fetcher=fetcher, hosts=listed, shelf=shelf, **more)


async def test_gitf09_the_tool_fetches_hands_over_counts_and_journals(
    https_host: tuple[GitHttpHost, Path, str, int], tmp_path: Path
) -> None:
    _, seed, ca, port = https_host
    shelf = _Shelf()
    volume = _Volume(64 * MIB)
    tools = _tools(_local(tmp_path, port, ca), shelf, volume=volume, journal_events=True)
    catalog = RepositoryCatalog(_EmptyCatalog(), tools)
    call = _call(f"https://{HOST}/acme/widgets")
    assert catalog.describe_call(call) == repository_effect()
    assert [s.name for s in catalog.schemas() or []] == [REPOSITORY_FETCH]
    done = await catalog.execute(call, CTX)
    assert done.status == "ok", done.content
    answer = json.loads(str(done.content))
    assert answer["path"].startswith("/pipeshub/repos/") and answer["path"].endswith(".git")
    assert (answer["label"], answer["readOnly"], answer["commit"]) == (f"{HOST}/acme/widgets", True, git("rev-parse", "HEAD", cwd=seed))
    with tarfile.open(fileobj=io.BytesIO(shelf.received[0])) as archive:
        assert f"{answer['path'].rsplit('/', 1)[1]}/HEAD" in archive.getnames()
    (event,) = done.events
    assert event.type == FETCHED_EVENT and not event.critical
    assert event.data == {
        "host": HOST, "repository": "acme/widgets", "bytes": answer["bytes"], "cached": False, "full": False,
        "commit": answer["commit"],
    }
    moved = volume.volume.volume(org_id="org-1", run_id="run-1")
    assert moved is not None and moved.down == answer["bytes"] and moved.by_alias["repository"][1] == answer["bytes"]
    # A second call is served by the cache: nothing more is counted, and no event without Node's word.
    quiet = _tools(tools.fetcher, _Shelf(), volume=volume)
    again = await quiet.execute(call, CTX)
    assert json.loads(str(again.content))["cached"] is True and again.events == ()
    assert volume.volume.volume(org_id="org-1", run_id="run-1").down == answer["bytes"]  # type: ignore[union-attr]


class _EmptyCatalog:
    def describe_call(self, call: ToolCall) -> None:
        return None

    def schemas(self) -> None:
        return None

    async def execute(self, call: ToolCall, ctx: ToolContext) -> Any:  # noqa: ANN401
        raise AssertionError("not this catalog's call")


async def test_gitf09_the_tool_refuses_what_the_policy_the_level_the_cap_or_the_turn_does_not_allow(
    https_host: tuple[GitHttpHost, Path, str, int], tmp_path: Path
) -> None:
    _, _, ca, port = https_host
    fetcher = _local(tmp_path, port, ca)
    call = _call(f"https://{HOST}/acme/widgets")

    async def refused(tools: RepositoryFetchTools, c: ToolCall = call) -> str:
        done = await tools.execute(c, CTX)
        assert done.status == "error"
        return str(done.content)

    assert "not on the git hosts" in await refused(_tools(fetcher, _Shelf(), hosts=[]))
    assert "not on the git hosts" in await refused(_tools(fetcher, _Shelf(), hosts=[{"host": HOST, "prefixes": ["other/"]}]))
    assert "classification" in await refused(_tools(fetcher, _Shelf(), egress=lambda: "deny"))
    assert "https" in await refused(_tools(fetcher, _Shelf()), _call("git@github.com:a/b.git"))
    capped = _Volume(10)
    _ = await capped.charge(org_id="org-1", run_id="run-1", received=11)
    assert "as much data" in await refused(_tools(fetcher, _Shelf(), volume=capped))
    small = _Volume(1024)
    assert "larger than" in await refused(_tools(fetcher, _Shelf(), volume=small)), "the run's remaining cap bounds the clone"
    assert "no sandbox" in await refused(_tools(fetcher, _Shelf(None)))

    class _Down:
        async def charge(self, **kwargs: Any) -> tuple[bool, int | None]:  # noqa: ANN401
            raise ConnectionError

    assert "cannot be counted" in await refused(_tools(fetcher, _Shelf(), volume=_Down()))


def test_gitf10_the_egress_volume_counts_a_charge_against_the_run_cap() -> None:
    volume = EgressVolume(VolumeLimits(max_run_bytes=1000), clock=SystemClock())
    first = volume.charge(org_id="o", run_id="r", received=0)
    assert (first.allowed, first.remaining) == (True, 1000)
    assert volume.charge(org_id="o", run_id="r", received=600).remaining == 400
    over = volume.charge(org_id="o", run_id="r", received=600)
    assert not over.allowed and over.remaining == 0
    assert volume.charge(org_id="o", run_id="other", received=0).allowed
    unlimited = EgressVolume(clock=SystemClock())
    assert unlimited.charge(org_id="o", run_id="r", received=5).remaining is None


# --- GITF-11: ph git ------------------------------------------------------------------------------------------------


def test_gitf11_ph_git_clone_and_fetch_call_the_tool_and_check_the_copy_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed = working_copy(tmp_path / "seed")
    commit(seed, "README.md", "one\n")
    first = bare_repository(tmp_path / "copies" / "1", "x", seed)
    calls: list[dict[str, Any]] = []
    answers = [
        {"path": str(first), "url": "https://github.com/acme/widgets", "label": "github.com/acme/widgets", "bytes": 3 * MIB},
    ]

    def fake_call(gw: Any, name: str, arguments: dict[str, Any], **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        assert name == ph.REPOSITORY_FETCH
        calls.append(arguments)
        return {"content": [{"type": "text", "text": json.dumps(answers.pop(0))}]}

    monkeypatch.setattr(ph, "call", fake_call)
    monkeypatch.chdir(tmp_path)
    out, err = io.StringIO(), io.StringIO()
    code = ph.main(["git", "clone", "https://github.com/acme/widgets", "--ref", "main"], out=out, err=err, gw=object())  # type: ignore[arg-type]
    assert code == 0, err.getvalue()
    assert calls == [{"url": "https://github.com/acme/widgets", "ref": "main"}]
    assert "Cloned github.com/acme/widgets into widgets (3 MB, read-only" in out.getvalue()
    # WP82-PH-1: the engine knows the copy is not saved with the session's files, nor its notes inside it.
    assert "not saved with the session's files: write your notes and outputs outside it" in out.getvalue()
    assert "pipeshub.fetched" not in out.getvalue()
    assert (tmp_path / "widgets" / "README.md").read_text() == "one\n"
    head = commit(seed, "TWO.md", "two\n")
    second = bare_repository(tmp_path / "copies" / "2", "x", seed)
    answers.append({"path": str(second), "url": "https://github.com/acme/widgets", "label": "github.com/acme/widgets"})
    code = ph.main(["git", "fetch", "widgets"], out=io.StringIO(), err=err, gw=object())  # type: ignore[arg-type]
    assert code == 0, err.getvalue()
    assert calls[-1] == {"url": "https://github.com/acme/widgets", "ref": "main"}
    assert git("rev-parse", "origin/main", cwd=tmp_path / "widgets") == head
    # A folder ph did not clone, and a clone over a folder in use, are refused before anything is asked.
    plain = working_copy(tmp_path / "plain")
    assert ph.main(["git", "fetch", str(plain)], out=io.StringIO(), err=io.StringIO(), gw=object()) == ph.EX_USAGE  # type: ignore[arg-type]
    assert ph.main(["git", "clone", "https://github.com/acme/widgets"], out=io.StringIO(), err=io.StringIO(), gw=object()) == ph.EX_USAGE  # type: ignore[arg-type]
    assert len(calls) == 2


def test_gitf11_a_clone_held_for_the_person_prints_the_hold_and_checks_nothing_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def held(gw: Any, name: str, arguments: dict[str, Any], **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        return {
            "content": [{"type": "text", "text": "Waiting for the person to approve. End your turn."}],
            "structuredContent": {"disposition": "deferred"},
        }

    monkeypatch.setattr(ph, "call", held)
    monkeypatch.chdir(tmp_path)
    out = io.StringIO()
    assert ph.main(["git", "clone", "https://github.com/acme/widgets"], out=out, err=io.StringIO(), gw=object()) == ph.EX_TEMPFAIL  # type: ignore[arg-type]
    assert "approve" in out.getvalue() and not (tmp_path / "widgets").exists()

    def denied(gw: Any, name: str, arguments: dict[str, Any], **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        return {"content": [{"type": "text", "text": "The person denied this call."}], "isError": True}

    monkeypatch.setattr(ph, "call", denied)
    err = io.StringIO()
    assert ph.main(["git", "clone", "https://github.com/acme/widgets"], out=io.StringIO(), err=err, gw=object()) == ph.EX_TOOL_ERROR  # type: ignore[arg-type]
    assert "denied" in err.getvalue() and not (tmp_path / "widgets").exists()


# --- GITF-12: GIT-FLOW's push asks too ------------------------------------------------------------------------------


class _RecordingSandbox:
    """A sandbox whose one command is recorded and answered with ``code``."""

    def __init__(self, code: int, out: bytes = b"", err: bytes = b"") -> None:
        self.code, self.out, self.err = code, out, err
        self.execs: list[tuple[list[str], dict[str, str]]] = []

    async def exec(self, argv: Sequence[str], *, env: dict[str, str], stdin: bool = False) -> Any:  # noqa: ANN401
        self.execs.append((list(argv), dict(env)))
        sandbox = self

        class _P:
            @property
            def stdout(self) -> AsyncIterator[bytes]:
                return _once(sandbox.out)

            @property
            def stderr(self) -> AsyncIterator[bytes]:
                return _once(sandbox.err)

            async def wait(self, *, timeout_s: float | None = None) -> int:
                return sandbox.code

        return _P()


async def _once(data: bytes) -> AsyncIterator[bytes]:
    yield data


def _checkout() -> Any:  # noqa: ANN401
    from harness_core.engines.git_checkout import GitCheckout, session_branch

    return GitCheckout(url="http://git.example.com/acme/widgets.git", session_branch=session_branch("s1"))


async def test_gitf12_a_push_runs_only_the_session_branch_with_the_placeholder_in_that_one_process() -> None:
    from harness_core.engines.git_checkout import PUSH_SCRIPT, push_session_branch

    checkout = _checkout()
    sandbox = _RecordingSandbox(0, out=b"To x\n4f83564c1f0e8596be3f71d78f847a96447a3bdd\n")
    pushed = await push_session_branch(sandbox, checkout, workspace_root="/workspace", placeholder="pxh-push-1")  # type: ignore[arg-type]
    assert pushed.pushed and pushed.commit == "4f83564c1f0e8596be3f71d78f847a96447a3bdd"
    [(argv, env)] = sandbox.execs
    assert argv[:3] == ["sh", "-c", PUSH_SCRIPT] and argv[-2:] == ["/workspace/repo", checkout.session_branch]
    assert "--force" not in PUSH_SCRIPT and '"HEAD:refs/heads/$branch"' in PUSH_SCRIPT
    from harness_core.engines.git_checkout import basic_auth

    assert f"Authorization: Basic {basic_auth('pxh-push-1')}" in env.values()
    refused = _RecordingSandbox(1, err=b"! [rejected] non-fast-forward pxh-push-1\n")
    failed = await push_session_branch(refused, checkout, workspace_root="/workspace", placeholder="pxh-push-1")  # type: ignore[arg-type]
    assert not failed.pushed and "pxh-push-1" not in failed.detail and "rejected" in failed.detail


async def test_gitf12_repository_push_asks_every_time_and_reports_what_happened() -> None:
    from app.harness.repositories import (
        PUSH_TOOL_ID,
        REPOSITORY_PUSH,
        RepositoryPushTools,
        push_destination,
        push_effect,
    )
    from harness_core.engines.git_checkout import PushOutcome

    checkout = _checkout()
    effect = push_effect()
    assert effect.tool_id == PUSH_TOOL_ID and effect.effect == "write" and sends_out(effect)
    call = ToolCall(id="p1", name=REPOSITORY_PUSH, arguments={})
    policy = RepositoryAsks(_allow, _Ledger(), "r", None, size_of=lambda location, c: None, cap_bytes=1, push=checkout)
    assert policy(call, effect) == "ask"
    reason = policy.ask_reason(call, effect)
    assert reason is not None and reason["conversationGrant"] is True
    where = push_destination(checkout)
    assert where.label == f"{checkout.session_branch} on git.example.com/acme/widgets"
    assert reason["destination"] == {"label": where.label, "key": where.key}

    from dataclasses import replace as replaced

    from app.harness.repositories import PUSH_GRANT_TTL_S
    from harness_core.engines.git_checkout import SealedGitCredentials
    from harness_core.sandbox.egress.sealed import CredentialSealer, new_placeholder

    sealer = CredentialSealer(b"gitf-12")
    pushable = replaced(checkout, credentials=SealedGitCredentials(sealer, read="u:read", push="u:push"))

    class _Pushes:
        def __init__(self, outcome: PushOutcome | None) -> None:
            self.outcome = outcome

        async def push_session_branch(self, grant: Any) -> PushOutcome | None:  # noqa: ANN401
            if self.outcome is not None:
                await grant("sbx-1", new_placeholder())
            return self.outcome

    class _Grants:
        def __init__(self) -> None:
            self.issued: list[dict[str, Any]] = []

        async def grant_push(self, **grant: Any) -> None:  # noqa: ANN401
            self.issued.append(grant)

    grants = _Grants()
    ok = await RepositoryPushTools(pushable, _Pushes(PushOutcome(True, "abc", "")), grants).execute(call, CTX)
    assert ok.status == "ok" and json.loads(str(ok.content))["branch"] == checkout.session_branch
    # GITF-19: one grant per approved push, for this sandbox, repository and branch, short-lived, and the push
    # credential sealed for that placeholder alone.
    [issued] = grants.issued
    assert (issued["sandbox_id"], issued["host"], issued["repository"], issued["branch"], issued["ttl_s"]) == (
        "sbx-1", "git.example.com", "acme/widgets", checkout.session_branch, PUSH_GRANT_TTL_S
    )
    from harness_core.sandbox.types import CredentialInjection, TokenRoute

    route = TokenRoute(issued["placeholder"], "git.example.com", issued["credential_ref"], CredentialInjection.BASIC)
    assert sealer.open(route) == "u:push"
    no = await RepositoryPushTools(pushable, _Pushes(PushOutcome(False, None, "rejected")), grants).execute(call, CTX)
    assert no.status == "error" and "fast-forwards" in str(no.content)
    gone = await RepositoryPushTools(pushable, _Pushes(None), grants).execute(call, CTX)
    assert gone.status == "error"
    unsealed = await RepositoryPushTools(checkout, _Pushes(PushOutcome(True, "abc", "")), grants).execute(call, CTX)
    assert unsealed.status == "error" and "no push credential" in str(unsealed.content)


def test_gitf12_ph_git_push_calls_repository_push(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, dict[str, Any]]] = []

    def pushed(gw: Any, name: str, arguments: dict[str, Any], **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        seen.append((name, arguments))
        answer = {"branch": "pipeshub/session-s1", "repository": "github.com/acme/widgets", "commit": "abc"}
        return {"content": [{"type": "text", "text": json.dumps(answer)}]}

    monkeypatch.setattr(ph, "call", pushed)
    out = io.StringIO()
    assert ph.main(["git", "push"], out=out, err=io.StringIO(), gw=object()) == 0  # type: ignore[arg-type]
    assert seen == [(ph.REPOSITORY_PUSH, {})]
    assert "Pushed abc to pipeshub/session-s1 of github.com/acme/widgets" in out.getvalue()


# --- GITF-26: the deployment's cap, the same fold as Node's -------------------------------------------------------


def test_gitf26_the_deployment_s_cap_holds_the_release_s_presets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(edition_config, "org_network_layers", None, raising=False)
    assert live_repository_hosts({"deployment": {"hosts": []}}, None, web=True) == []
    owner = {"deployment": {"hosts": [{"host": "github.com", "prefixes": ["onyx-dot-app/"]}]}}
    # A cap limited to an owner does not hold a whole host.
    assert live_repository_hosts(owner, None, web=True) == []
    assert live_repository_hosts({"deployment": {"hosts": [{"host": "gitlab.com"}]}}, None, web=True) == [
        {"host": "gitlab.com", "preset": "gitlab-public"}
    ]
    assert capped_hosts(None, {"hosts": [{"host": "github.com"}]}) == [{"host": "github.com"}]
    assert capped_hosts({"hosts": [{"host": "github.com"}]}, None) == []


def test_gitf26_an_edition_s_layers_fold_the_member_and_are_checked_again(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[object] = []

    class Layers:
        def sites(self, websites: object, agent_key: str | None) -> list[Mapping[str, Any]]:
            return []

        def repository_hosts(self, repositories: object, agent_key: str | None, default: object) -> list[Mapping[str, Any]]:
            seen.append((repositories, agent_key, default))
            return [{"host": "git.example.com"}, {"host": "git.corp"}]

    monkeypatch.setattr(edition_config, "org_network_layers", Layers(), raising=False)
    assert live_repository_hosts(None, "a1", web=False) == [{"host": "git.example.com"}]
    assert seen == [({}, "a1", None)]


# --- GITF-25: the repository egress label --------------------------------------------------------------------------


def test_gitf25_clone_bytes_count_under_repository_and_alert_as_it() -> None:
    from harness_core.gateway.refusal_reports import EGRESS_ALIASES, EgressVolumeAlert
    from harness_core.sandbox.egress.volume import (  # pyright: ignore[reportPrivateUsage]
        _SLOTS,
        _restored_run,
    )

    assert EGRESS_ALIASES[-1] == "repository" and EGRESS_ALIASES[:6] == (
        "model-gateway", "tool-gateway", "tunnel", "intercepted", "database", "other"
    )
    volume = EgressVolume(VolumeLimits(max_run_bytes=0), clock=SystemClock())
    _ = volume.charge(org_id="o", run_id="r", received=5 * MIB)
    moved = volume.volume(org_id="o", run_id="r")
    assert moved is not None and moved.by_alias["repository"] == (0, 5 * MIB) and moved.by_alias["other"] == (0, 0)
    alert = EgressVolumeAlert.of("run_threshold", "down", 5 * MIB, MIB, "repository")
    assert alert is not None and alert.alias == "repository"
    from harness_core.contracts import validate_contract
    from harness_core.contracts.generated.events.egress_volume_alert import (
        EgressVolumeAlertData,
    )

    _ = validate_contract(EgressVolumeAlertData, {
        "kind": "run_threshold", "direction": "down", "bytes": 5 * MIB, "thresholdBytes": MIB, "alias": "repository",
    })
    # A run's record a proxy wrote before the label existed still reads, the new label at zero.
    old = {"orgId": "o", "runId": "r", "slots": [1] * (_SLOTS - 2), "total": _SLOTS - 2, "capped": False}
    restored = _restored_run(old, 0.0)
    assert restored.slots[-2:] == [0, 0] and sum(restored.slots) == _SLOTS - 2


# --- GITF-24: the repository folder's own mount ---------------------------------------------------------------------


def test_gitf24_handed_over_repositories_get_their_own_read_only_mount_sized_to_the_cap(tmp_path: Path) -> None:
    from app.harness.engines.worker import controller_config
    from harness_core.engines.controller import (
        EngineControllerConfig,
        engine_sandbox_spec,
    )
    from harness_core.repos.fetch import HAND_OFF_DIR, HAND_OFF_SLACK_MB, hand_off_mount
    from harness_core.sandbox.pool import WarmShape, shape_of
    from harness_core.sandbox.types import MountSpec, RuntimeMode

    assert hand_off_mount(FetchConfig(cache_dir=tmp_path)) == (HAND_OFF_DIR, 500 + HAND_OFF_SLACK_MB)
    assert hand_off_mount(FetchConfig(cache_dir=tmp_path, repo_max_bytes=8 * MIB)) == (HAND_OFF_DIR, 8 + HAND_OFF_SLACK_MB)
    assert controller_config(()).repos_mount == (HAND_OFF_DIR, 500 + HAND_OFF_SLACK_MB)
    config = EngineControllerConfig(images={}, repos_mount=(HAND_OFF_DIR, 516))
    spec = engine_sandbox_spec(
        config, image="img", mode=RuntimeMode.DOCKER_HARDENED, workspace=MountSpec("/workspace"), labels={},
        placeholders=("pxh-m", "pxh-t"), tools_upstream="http://tool-host/x",
    )
    [repos] = [m for m in spec.mounts if m.target == HAND_OFF_DIR]
    assert (repos.read_only, repos.source, repos.size_mb) == (True, None, 516)
    assert spec.seedable(f"{HAND_OFF_DIR}/abc.git") and spec.resources.scratch_mb != 516, "the session tmpfs is unchanged"
    shape = shape_of(spec)
    assert shape is not None and shape.mount_sizes == ((HAND_OFF_DIR, 516),)
    assert WarmShape.from_wire(shape.to_wire()) == shape
    assert next(m for m in shape.template().mounts if m.target == HAND_OFF_DIR).size_mb == 516
    unsized = shape_of(engine_sandbox_spec(
        EngineControllerConfig(images={}), image="img", mode=RuntimeMode.DOCKER_HARDENED,
        workspace=MountSpec("/workspace"), labels={}, placeholders=("pxh-m", "pxh-t"), tools_upstream="http://t/x",
    ))
    assert unsized is not None and '"sizes"' not in unsized.to_wire(), "a shape without sized mounts keeps its digest"
    for bad in ({"size_mb": 0}, {"size_mb": 5, "source": "o/s/w"}):
        with pytest.raises(ValueError):
            _ = MountSpec("/x", **bad)  # type: ignore[arg-type]


# --- GITF-33: a clone refused for its host is the "Clone not available" card ----------------------------------------


async def test_gitf33_a_host_not_on_the_list_journals_the_unavailable_card(tmp_path: Path) -> None:
    from app.harness.repositories import UNAVAILABLE_EVENT

    fetcher = RepositoryFetcher(FetchConfig(cache_dir=tmp_path / "c"), _Resolver({}))
    tools = _tools(fetcher, _Shelf(), hosts=[{"host": "gitlab.com"}], journal_events=True)
    done = await tools.execute(_call("https://github.com/onyx-dot-app/onyx"), CTX)
    assert done.status == "error"
    (event,) = done.events
    assert event.type == UNAVAILABLE_EVENT and not event.critical
    assert event.data == {"host": "github.com", "path": "/onyx-dot-app/onyx", "reason": "host_not_allowed"}
    # Another refusal, or a control plane that does not take the events, journals nothing.
    assert (await tools.execute(_call("git@github.com:a/b.git"), CTX)).events == ()
    quiet = _tools(fetcher, _Shelf(), hosts=[], journal_events=False)
    assert (await quiet.execute(_call("https://github.com/onyx-dot-app/onyx"), CTX)).events == ()


# --- GITF-34: the disk guard and the cache's state for the git hosts screen ----------------------------------------


async def test_gitf34_the_cache_never_grows_into_its_disk_reserve_and_says_so(
    https_host: tuple[GitHttpHost, Path, str, int], tmp_path: Path
) -> None:
    from app.harness.repositories import REPOSITORY_CACHE_KEY, CacheStatusPublisher

    _, _, ca, port = https_host
    gib = 1024**3
    # A 100 GB volume keeps 5 GB (GITF-38): 50 GB free fetches; 5.2 GB free cannot take a 500 MB clone.
    free = [50 * gib]
    fetcher = _LoopbackFetcher(
        FetchConfig(cache_dir=tmp_path / "cache", git_bin="git", port=port, ca_file=ca), _Resolver({}),
    )
    fetcher.disk_usage = lambda _path: (100 * gib, free[0])
    fetched = await fetcher.fetch("org-1", LOCATION, ref=None, full=False)
    free[0] = int(5.2 * gib)
    assert fetcher.disk_low()
    with pytest.raises(RepositoryRefused) as low:
        _ = await fetcher.fetch("org-1", RepositoryLocation(HOST, "acme/huge"), ref=None, full=False)
    assert low.value.reason == "repository_disk_low" and "5.2 GB free" in low.value.message
    assert (await fetcher.fetch("org-1", LOCATION, ref=None, full=False)).cached, "what was fetched before still works"

    class _Store:
        def __init__(self) -> None:
            self.written: list[tuple[str, dict[str, Any]]] = []

        async def set_config(self, key: str, value: dict[str, Any]) -> bool:
            self.written.append((key, value))
            return True

    store, now = _Store(), [0.0]
    publisher = CacheStatusPublisher(fetcher, store, every_s=60, clock=lambda: now[0])
    await publisher.publish()
    [(key, status)] = store.written
    assert key == REPOSITORY_CACHE_KEY
    assert {k: status[k] for k in ("totalMb", "perRepoMb", "concurrency", "diskLow")} == {
        "totalMb": 2048, "perRepoMb": 500, "concurrency": 2, "diskLow": True,
    }
    assert status["usedMb"] >= 1 and fetched.size_bytes > 0 and isinstance(status["updatedAt"], int)
    await publisher.publish()
    assert len(store.written) == 1, "unchanged and recent: not written again"
    free[0] = 50 * gib
    await publisher.publish()
    assert len(store.written) == 2 and store.written[-1][1]["diskLow"] is False, "a change is written at once"


# --- GITF-35: a hand-off that would not fit writes nothing -----------------------------------------------------------


async def test_gitf35_the_repository_folder_takes_a_complete_tar_or_nothing(tmp_path: Path) -> None:
    from harness_core.engines.controller import (  # pyright: ignore[reportPrivateUsage]
        HandOffTooLarge,
        _SavingEndpoint,
    )

    class _Sandbox:
        id = "sbx-1"

        def __init__(self) -> None:
            self.seeded: list[tuple[str, bytes]] = []

        async def seed_tar(self, dest: str, stream: AsyncIterator[bytes]) -> None:
            self.seeded.append((dest, b"".join([c async for c in stream])))

    async def tar(data: bytes) -> AsyncIterator[bytes]:
        yield data

    sandbox = _Sandbox()
    endpoint = _SavingEndpoint(cast("Any", None))
    assert await endpoint.hand_off(tar(b"x"), 1) is None, "no turn holds a sandbox yet"
    endpoint.shelf = (cast("Any", None), cast("Any", sandbox), cast("Any", logging.getLogger("t")))
    endpoint.repos, endpoint.repos_left = "/pipeshub/repos", 100
    assert endpoint.sandbox_id() == "sbx-1"
    assert await endpoint.hand_off(tar(b"a" * 60), 60) == "/pipeshub/repos"
    with pytest.raises(HandOffTooLarge):
        _ = await endpoint.hand_off(tar(b"b" * 60), 60)
    assert [len(data) for _, data in sandbox.seeded] == [60], "the refused one wrote nothing"

    # A fetch that fails (too large, refused, cut) never reaches the hand-off at all.
    class _Never:
        async def hand_off(self, archive: AsyncIterator[bytes], size_bytes: int) -> str | None:
            raise AssertionError("handed off before the fetch completed")

    class _Failing(RepositoryFetcher):
        async def fetch(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
            raise RepositoryRefused("repository_too_large", "too large")

    tools = _tools(_Failing(FetchConfig(cache_dir=tmp_path / "c"), _Resolver({})), cast("Any", _Never()))
    assert (await tools.execute(_call(f"https://{HOST}/acme/widgets"), CTX)).status == "error"


# --- R32-REPO: a clone the hosts refuse is not put to the person first ------------------------------------------------


async def test_r32repo_a_clone_the_hosts_refuse_is_never_asked_about_first() -> None:
    """MR-8's off journey (R32-REPO lane): with cloning turned off the person was asked to approve the clone, then it
    was refused. The ask now reads the run's hosts at each decision's refresh: a repository they do not allow runs
    (the tool refuses it with the "Clone not available" card), one they allow asks; unread hosts ask, as before."""
    hosts: list[dict[str, Any]] = []

    async def read() -> list[dict[str, Any]]:
        return hosts

    policy = RepositoryAsks(_allow, _Ledger(), "r", None, size_of=lambda location, c: None, cap_bytes=1, hosts=read)
    call, effect = _call("https://github.com/onyx-dot-app/onyx"), repository_effect()
    assert policy(call, effect) == "ask", "before a refresh nothing is known: ask"
    await policy.refresh()
    assert policy(call, effect) == "allow"
    hosts.append({"host": "github.com", "preset": "github-public"})
    await policy.refresh()
    assert policy(call, effect) == "ask"

    async def broken() -> list[dict[str, Any]]:
        raise RuntimeError("node down")

    unread = RepositoryAsks(_allow, _Ledger(), "r", None, size_of=lambda location, c: None, cap_bytes=1, hosts=broken)
    await unread.refresh()
    assert unread(call, effect) == "ask"
