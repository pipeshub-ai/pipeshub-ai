"""Round 14 (WP-69 move-staging x the turn's start): inputs placed when a turn starts are moved in as the sandbox user.

WP-69 placed a large input on its first read through a staging name and a move as the sandbox user, because
``put_tar`` writes as root and follows a link the engine planted. The inputs a turn starts with still went through
``put_tar`` straight to ``in/``: in a kept sandbox (a person's sign-in kept between turns, a keep-warm release) the
engine's background processes can replace ``in`` with a link while the archive streams. And the staging folder
itself showed up (empty) while the file's bytes were still being fetched, so a running engine could see its name and
swap it for a link before root wrote into it.

R14-TS-1 the turn's inputs are never written by ``put_tar`` to where they go; each lands by the move, the sandbox
         user's, and the workspace holds them as the manifest does, with nothing left behind.
R14-TS-2 when ``in`` turns into a link out of the workspace as the turn starts, nothing lands there; the input waits
         for its first read (``ph inputs get``) instead, the rest of the workspace is placed.
R14-TS-4 the same through the sandbox provider (WP-67): a suspended and resumed sandbox, a link left planted in it.
R14-TS-3 a staged file is one archive member directly under the root (no folder appears before its bytes), named
         with 128 random bits, at the turn's start and on a first read.
"""

from __future__ import annotations

import io
import re
import tarfile
from typing import TYPE_CHECKING, Any

from harness_core.sandbox.provider import BrokerSandboxProvider, SandboxProviderKind
from harness_core.testing.contracts.workspace_sync import LEASE, acquire, commit, tree
from harness_core.workspace.sync.tar_sync import STAGING_PREFIX
from harness_core.workspace.sync.types import ON_FIRST_READ
from tests.unit.harness_core.workspace.sync.support import ROOT, TAR, FakeSyncHarness

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from harness_core.sandbox.ports import SandboxHandle

STAGED = re.compile(rf"^{re.escape(STAGING_PREFIX)}[0-9a-f]{{32}}$")


class Recording:
    """The sandbox as the sync sees it, recording each ``put_tar``'s members; ``after_put`` runs after one, as an
    engine's background process would between two of the sync's steps."""

    def __init__(self, inner: SandboxHandle, after_put: Callable[[], Awaitable[None]] | None = None) -> None:
        self._inner = inner
        self.archives: list[list[tarfile.TarInfo]] = []
        self._after_put = after_put

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401
        return getattr(self._inner, name)

    async def put_tar(self, dest: str, stream: AsyncIterator[bytes]) -> None:
        data = b"".join([chunk async for chunk in stream])
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
            self.archives.append(archive.getmembers())

        async def again() -> AsyncIterator[bytes]:
            yield data

        await self._inner.put_tar(dest, again())
        if self._after_put is not None:
            hook, self._after_put = self._after_put, None
            await hook()


def _names(recording: Recording) -> list[list[str]]:
    return [[m.name for m in members] for members in recording.archives]


async def test_r14_ts_1_a_turns_inputs_are_moved_in_never_written_by_put_tar() -> None:
    h = FakeSyncHarness(TAR)
    manifest = await commit(h, {"in/sales.csv": b"region,amount\n", "in/deep/q3.pdf": b"%PDF", "notes.md": b"n"})
    sandbox = Recording(await acquire(h))
    state = await h.sync.materialize(sandbox, manifest, root=ROOT)  # type: ignore[arg-type]
    written = [name for names in _names(sandbox) for name in names]
    assert not [name for name in written if name == "in" or name.startswith("in/")], written
    assert "notes.md" in written
    assert await tree(sandbox, ROOT) == {  # type: ignore[arg-type]
        path: (entry.mode, data)
        for path, entry, data in (
            ("in/sales.csv", manifest.files["in/sales.csv"], b"region,amount\n"),
            ("in/deep/q3.pdf", manifest.files["in/deep/q3.pdf"], b"%PDF"),
            ("notes.md", manifest.files["notes.md"], b"n"),
        )
    }
    assert state.hidden == {}
    assert not any(STAGING_PREFIX in path for path in sandbox.files)
    collected = await h.sync.collect(sandbox, state)  # type: ignore[arg-type]
    assert collected.changes.paths == frozenset(), "the next save finds nothing changed"


async def test_r14_ts_2_an_input_folder_turned_into_a_link_at_the_turns_start_receives_nothing() -> None:
    h = FakeSyncHarness(TAR)
    manifest = await commit(h, {"in/sales.csv": b"region,amount\n", "notes.md": b"n"})
    inner = await acquire(h)

    async def plant() -> None:
        await h.hands.mkdir(inner, "/tmp/elsewhere")
        await h.hands.remove(inner, f"{ROOT}/in")
        await h.hands.symlink(inner, f"{ROOT}/in", "/tmp/elsewhere")

    sandbox = Recording(inner, after_put=plant)
    state = await h.sync.materialize(sandbox, manifest, root=ROOT)  # type: ignore[arg-type]
    files = sandbox.files
    assert not any(path.startswith("/tmp/elsewhere/") for path in files), sorted(files)
    assert not any(STAGING_PREFIX in path for path in files)
    assert state.hidden == {"in/sales.csv": ON_FIRST_READ}, "it waits for its first read"
    assert (await tree(sandbox, ROOT))["notes.md"][1] == b"n"  # type: ignore[arg-type]


async def test_r14_ts_3_a_staged_file_is_one_member_under_the_root_with_no_folder_before_its_bytes() -> None:
    h = FakeSyncHarness(TAR)
    manifest = await commit(h, {"in/sales.csv": b"s", "in/big.pdf": b"%PDF big"})
    sandbox = Recording(await acquire(h))
    state = await h.sync.materialize(sandbox, manifest, root=ROOT, defer={"in/big.pdf"})  # type: ignore[arg-type]
    _ = await h.sync.stage(sandbox, state, "in/big.pdf")  # type: ignore[arg-type]
    staged = [members for members in sandbox.archives if any(STAGING_PREFIX in m.name for m in members)]
    assert len(staged) == 2, _names(sandbox)
    for members in staged:
        [member] = members
        assert member.isfile() and STAGED.match(member.name), member.name
    assert len({members[0].name for members in staged}) == 2


async def test_r14_ts_4_through_the_provider_a_resumed_sandboxs_new_input_is_moved_in_past_a_planted_link() -> None:
    """WP-67 x WP-69: the run code reaches sandboxes only through the provider; a sandbox it suspends and resumes for
    the next turn is the kept sandbox the move guards, and the provider's handles carry the move."""
    h = FakeSyncHarness(TAR)
    provider = BrokerSandboxProvider(h.broker, SandboxProviderKind.DOCKER)
    first = await commit(h, {"notes.md": b"n"})
    sandbox = await provider.create(h.spec(), lease=LEASE, key="r14-kept")
    _ = await h.sync.materialize(sandbox, first, root=ROOT)
    await provider.suspend(sandbox, keep_s=60)
    kept = await provider.resume(h.spec(), lease=LEASE, key="r14-kept")
    assert kept.id == sandbox.id
    # A process the last turn left running made ``in`` a link out of the workspace.
    await h.hands.mkdir(kept, "/tmp/elsewhere")
    await h.hands.symlink(kept, f"{ROOT}/in", "/tmp/elsewhere")
    second = await commit(h, {"in/new.csv": b"x"})
    state = await h.sync.materialize(kept, second, root=ROOT)
    assert not any(path.startswith("/tmp/elsewhere/") for path in kept.files)  # type: ignore[attr-defined]
    assert "in/new.csv" in await tree(kept, ROOT) or state.hidden == {"in/new.csv": ON_FIRST_READ}
    await provider.destroy(kept)
