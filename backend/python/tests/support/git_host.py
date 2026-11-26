"""A local git host for GIT-FLOW tests: bare repositories served by ``git http-backend``, no network.

``GitHttpHost`` serves smart HTTP over loopback and records every request's ``Authorization`` header, so a test can
show which credential reached the host. ``HostSandbox`` runs a sandbox's commands as host processes (the git client
is the host's own ``git``): enough for the proxy and the git rule, not an isolation boundary.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from aiohttp import web

from harness_core.sandbox.egress.trust import PROXY_URL

if TYPE_CHECKING:
    import ssl
    from collections.abc import AsyncIterator, Mapping, Sequence
    from pathlib import Path

GIT_ENV = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.test",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.test",
    "GIT_TERMINAL_PROMPT": "0",
}


def git(*args: str, cwd: Path | None = None, env: Mapping[str, str] | None = None, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env={"PATH": os.environ["PATH"], "HOME": str(cwd or "/tmp"), **GIT_ENV, **(env or {})},
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if check and result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout.strip()


async def agit(*args: str, cwd: Path, env: Mapping[str, str] | None = None, check: bool = True) -> str:
    """``git`` without blocking the loop: for commands that go through a proxy this test's loop serves."""
    process = await asyncio.create_subprocess_exec(
        "git", *args, cwd=cwd, env={"PATH": os.environ["PATH"], "HOME": str(cwd), **GIT_ENV, **(env or {})},
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await asyncio.wait_for(process.communicate(), 60)
    if check and process.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {err.decode()}")
    return out.decode().strip()


def commit(work: Path, name: str, content: str = "x\n") -> str:
    path = work / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    git("add", "-A", cwd=work)
    git("commit", "-q", "-m", f"add {name}", cwd=work)
    return git("rev-parse", "HEAD", cwd=work)


def bare_repository(root: Path, repository: str, seed: Path) -> Path:
    """``root/<repository>.git`` with ``seed``'s branches and tags."""
    bare = root / f"{repository}.git"
    bare.parent.mkdir(parents=True, exist_ok=True)
    git("init", "-q", "--bare", "-b", "main", str(bare))
    git("push", "-q", str(bare), "--all", cwd=seed)
    git("push", "-q", str(bare), "--tags", cwd=seed)
    return bare


def working_copy(path: Path) -> Path:
    path.mkdir(parents=True)
    git("init", "-q", "-b", "main", cwd=path)
    return path


@dataclass
class GitHttpHost:
    root: Path
    authorizations: list[str | None] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    port: int = 0
    # A path prefix -> where a request under it is redirected (302), as a hostile host would send git elsewhere.
    redirects: dict[str, str] = field(default_factory=dict[str, str])
    # Seconds each request waits before it is answered (a host that hangs).
    delay_s: float = 0.0
    _runner: web.AppRunner | None = None

    async def start(self, host: str = "127.0.0.1", *, tls: ssl.SSLContext | None = None) -> str:
        """``host``: the address to serve on (a rig bridge's gateway, for a proxy in a container); ``tls``: serve https."""
        app = web.Application(client_max_size=256 * 1024 * 1024)
        app.router.add_route("*", "/{tail:.*}", self._handle)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, host, 0, ssl_context=tls)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        return f"{'https' if tls is not None else 'http'}://{host}:{self.port}"

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()

    async def _handle(self, request: web.Request) -> web.StreamResponse:
        self.authorizations.append(request.headers.get("Authorization"))
        self.paths.append(request.path_qs)
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        for prefix, location in self.redirects.items():
            if request.path.startswith(prefix):
                return web.Response(status=302, headers={"Location": location})
        body = await request.read()
        env = {
            "PATH": os.environ["PATH"],
            "GIT_PROJECT_ROOT": str(self.root),
            "GIT_HTTP_EXPORT_ALL": "1",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "REMOTE_USER": "tester",
            "REQUEST_METHOD": request.method,
            "PATH_INFO": request.path,
            "QUERY_STRING": request.query_string,
            "CONTENT_TYPE": request.headers.get("Content-Type", ""),
            "CONTENT_LENGTH": str(len(body)),
        }
        if "Content-Encoding" in request.headers:
            env["HTTP_CONTENT_ENCODING"] = request.headers["Content-Encoding"]
        if "Git-Protocol" in request.headers:
            env["GIT_PROTOCOL"] = request.headers["Git-Protocol"]
        process = await asyncio.create_subprocess_exec(
            "git", "http-backend", env=env, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE
        )
        out, _ = await process.communicate(body)
        head, _, payload = out.partition(b"\r\n\r\n")
        if not _ and b"\n\n" in out:
            head, _, payload = out.partition(b"\n\n")
        status = 200
        headers: dict[str, str] = {}
        for line in head.decode("latin-1").splitlines():
            key, _, value = line.partition(":")
            if key.lower() == "status":
                status = int(value.strip().split(" ", 1)[0])
            elif key:
                headers[key.strip()] = value.strip()
        return web.Response(status=status, body=payload, headers=headers)


class _Process:
    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self._process = process

    @property
    def stdout(self) -> AsyncIterator[bytes]:
        return self._read(self._process.stdout)

    @property
    def stderr(self) -> AsyncIterator[bytes]:
        return self._read(self._process.stderr)

    @staticmethod
    async def _read(stream: asyncio.StreamReader | None) -> AsyncIterator[bytes]:
        assert stream is not None
        while chunk := await stream.read(65536):
            yield chunk

    async def write(self, data: bytes) -> None:
        raise NotImplementedError

    async def close_stdin(self) -> None:
        return None

    async def signal(self, sig: int) -> None:
        self._process.send_signal(sig)

    async def wait(self, *, timeout_s: float | None = None) -> int:
        return await asyncio.wait_for(self._process.wait(), timeout_s)


@dataclass
class HostSandbox:
    """A ``SandboxHandle`` whose ``exec`` runs on the host with only the environment it is given (and ``PATH``)."""

    home: Path
    # Where the sandbox's proxy variables point instead of ``egress-proxy`` (the host cannot resolve that name).
    proxy_url: str | None = None
    id: str = "sbx-host"
    execs: list[tuple[tuple[str, ...], dict[str, str]]] = field(default_factory=list)

    async def exec(self, argv: Sequence[str], *, env: Mapping[str, str], stdin: bool = False) -> _Process:
        given = dict(env)
        if self.proxy_url is not None:
            given = {k: v.replace(PROXY_URL, self.proxy_url) for k, v in given.items()}
        full = {"PATH": os.environ["PATH"], "HOME": str(self.home), **given}
        self.execs.append((tuple(argv), dict(env)))
        process = await asyncio.create_subprocess_exec(
            *argv, env=full, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        return _Process(process)


def https_pki(names: Sequence[str]) -> tuple[str, str, str]:
    """A test authority and one certificate for ``names``: ``(ca, cert, key)`` PEMs, for an https git host."""
    import datetime as dt

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    now = dt.datetime.now(dt.UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "pxh git host test authority")])
    ca = (
        x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(hours=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    cert = (
        x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[0])]))
        .issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(hours=1)).not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(n) for n in names]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    pem = serialization.Encoding.PEM
    key_pem = key.private_bytes(pem, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return ca.public_bytes(pem).decode(), cert.public_bytes(pem).decode(), key_pem.decode()
