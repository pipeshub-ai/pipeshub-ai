"""R37-SECC proof tests.

Each test asserts the secure behaviour and is a strict xfail until its SEC-C finding is fixed: the gate stays
green while the hole is open and goes red the moment the fix lands, so the marker must then be removed.
"""

from __future__ import annotations

import io
import tarfile
import zipfile
import zlib
from typing import TYPE_CHECKING

import aiohttp
import pytest
from aiohttp import web

from harness_core.gates import verify
from harness_core.sandbox.auth import StaticBearerAuth
from harness_core.sandbox.egress import (
    EgressBinding,
    EgressProxy,
    InMemoryEgressBindings,
    InMemoryTokenSource,
    ProxyLimits,
    StaticResolver,
)
from harness_core.sandbox.egress.server import EgressServer
from harness_core.sandbox.types import EgressDestination, EgressPolicy, TokenRoute
from harness_core.testing.clock import ManualClock
from harness_core.testing.ids import SeededIds

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_MIB = 1024 * 1024


def test_sec_c_06_pasted_secret_redaction_is_linear(monkeypatch: pytest.MonkeyPatch) -> None:
    # A repository's AGENTS.md (up to 1 MiB) was redacted whole on the worker's event loop
    # (repository_instructions._file) by a trailing-punctuation pattern whose cost grew with the square of a dot run.
    # Proven without a clock: the pattern is linear by structure, and the file is cut before it is scanned.
    from harness_core.contracts.vectors.token_patterns import PASTED_INPUT_RULES
    from harness_core.engines import repository_instructions
    from harness_core.security import pasted_secrets
    from tests.support.regex_linearity import violations

    assert violations(PASTED_INPUT_RULES["contextual"]["trailing"]) == []
    scanned: list[int] = []
    original = pasted_secrets.redact_pasted_secrets

    def measure(window: str, mask: str = pasted_secrets.SECRET_MASK) -> tuple[str, tuple[object, ...]]:
        scanned.append(len(window))
        return original(window, mask)

    monkeypatch.setattr(pasted_secrets, "redact_pasted_secrets", measure)
    text = "token " + "." * 1_000_000 + "a"
    shown = repository_instructions._file("AGENTS.md", text.encode(), repository_instructions.MAX_FILE_CHARS)
    assert shown.truncated
    assert scanned and max(scanned) <= repository_instructions.MAX_FILE_CHARS + 64 * 1024


def _gzip_tar_declaring(size: int) -> bytes:
    """A gzip tar whose one member declares `size` bytes of zeros: a few hundred KiB on the wire."""
    header = tarfile.TarInfo("SKILL.md")
    header.size = size
    raw = header.tobuf(format=tarfile.GNU_FORMAT)
    out = io.BytesIO()
    compressor = zlib.compressobj(1, zlib.DEFLATED, 31)
    out.write(compressor.compress(raw))
    zeros = bytes(_MIB)
    for _ in range(size // _MIB):
        out.write(compressor.compress(zeros))
    out.write(compressor.compress(bytes(1024)))
    out.write(compressor.flush())
    return out.getvalue()


class _CountingBytesIO(io.BytesIO):
    consumed = 0

    def read(self, size: int | None = -1) -> bytes:
        chunk = super().read(size)
        _CountingBytesIO.consumed += len(chunk)
        return chunk


def test_sec_c_05_a_tar_member_over_the_limit_is_refused_from_its_header(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.skills import package_importer

    data = _gzip_tar_declaring(400 * _MIB)  # one member far over the 25 MiB member cap
    monkeypatch.setattr(package_importer.io, "BytesIO", _CountingBytesIO)
    _CountingBytesIO.consumed = 0
    with pytest.raises(package_importer.PackageImportError):
        package_importer._extract_tar(data)
    # Refusing from the first header needs the first compressed block, not the whole stream.
    assert _CountingBytesIO.consumed < len(data) // 4


def _deflated_zeros_zip(size: int) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive, archive.open(
        "bomb.bin", "w", force_zip64=True
    ) as member:
        zeros = bytes(_MIB)
        for _ in range(size // _MIB):
            member.write(zeros)
    return out.getvalue()


def test_sec_c_07_the_verification_gate_meters_inflated_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    data = _deflated_zeros_zip(256 * _MIB)
    inflated = 0
    real_read = zipfile.ZipExtFile.read

    def counting_read(self: zipfile.ZipExtFile, n: int = -1) -> bytes:
        nonlocal inflated
        chunk = real_read(self, n)
        inflated += len(chunk)
        return chunk

    monkeypatch.setattr(zipfile.ZipExtFile, "read", counting_read)
    verify._opens_as(data, "zip")
    # A file the sandbox wrote (up to the 100 MiB workspace limit) may not cost unbounded inflation on the
    # worker's loop: the gate's budget is a small multiple of the file, never the archive's declared size.
    assert inflated <= 64 * _MIB


PH = "ph-model-0123456789abcdef"
SECRET = b"secc-secret"
POLICY = EgressPolicy(
    destinations=(EgressDestination("model-gateway", (8097,)),),
    token_routes=(TokenRoute(PH, "model-gateway", "gateway:model"),),
)


async def _reflecting_reason(request: web.Request) -> web.StreamResponse:
    token = request.headers.get("Authorization", "").removeprefix("Bearer ")
    return web.Response(status=200, reason=f"OK {token}", text="ok")


@pytest.fixture
async def reflecting_proxy() -> AsyncIterator[int]:
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", _reflecting_reason)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    up_port = runner.addresses[0][1]
    clock = ManualClock()
    proxy = EgressProxy(
        bindings=InMemoryEgressBindings(),
        tokens=InMemoryTokenSource(SECRET, clock),
        resolver=StaticResolver(),
        upstreams={"model-gateway": f"http://127.0.0.1:{up_port}"},
        clock=clock,
        ids=SeededIds(),
        limits=ProxyLimits(max_body_bytes=1024),
    )
    server = EgressServer(proxy, StaticBearerAuth("control"), instance="secc-test")
    data_port, _ = await server.start(host="127.0.0.1", data_port=0, control_port=0)
    await proxy.bindings.register(EgressBinding("sbx-a", "127.0.0.1", "127.0.0.1", POLICY, {"runId": "r1"}))
    yield data_port
    await server.stop()
    await runner.cleanup()


async def test_sec_c_15_the_reason_phrase_never_carries_the_swapped_token(reflecting_proxy: int) -> None:
    token = InMemoryTokenSource.derive(SECRET, "sbx-a", "gateway:model", 0)
    async with aiohttp.ClientSession() as session, session.get(
        f"http://127.0.0.1:{reflecting_proxy}/v1/x",
        headers={"Host": "model-gateway:8097", "Authorization": f"Bearer {PH}"},
    ) as response:
        assert token not in (response.reason or "")
