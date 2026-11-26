"""R32-REPO: a public git host for the e2e stack, answering as github.com on the stack's network (never on a host port).

    python /e2e/pyrun.py /e2e/fake_git/server.py <state dir>

The worker's ``repository_fetch`` (WP-63 part A) clones ``https://github.com/<owner>/<repo>`` itself, outside every
sandbox, through the product's own ``RepositoryFetcher``: the address pinned, no redirects, the hardened git config.
This host stands for github.com: a test authority signs its certificate (``<state dir>/ca.pem``, which the worker
trusts through ``HARNESS_REPO_CA_FILE``), its address is private on the stack's network, and the stack's
``EGRESS_PRIVATE_ALLOW`` opens that one name, as an enterprise mirror would be opened. Nothing about the product's
checks changes. Smart HTTP over ``git http-backend``; every request's path is appended to ``<state dir>/requests.log``
so a journey can tell a declined clone fetched nothing; any ``pxh-e2e/<name>`` serves the same repository, so each
journey clones a name of its own.
"""

from __future__ import annotations

import datetime as dt
import os
import ssl
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# The repository a journey clones, and the README line its answer must quote (journeys.py REPO_MARKER).
OWNER = "pxh-e2e"
REPOSITORY = f"{OWNER}/journey-repo"
MARKER = "pxh-journey-repo-marker-7f3a: the journey repository's README"
NAMES = ("github.com",)
GIT_ENV = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_AUTHOR_NAME": "pxh",
    "GIT_AUTHOR_EMAIL": "pxh@example.test",
    "GIT_COMMITTER_NAME": "pxh",
    "GIT_COMMITTER_EMAIL": "pxh@example.test",
}


def git(*args: str, cwd: Path | None = None) -> None:
    subprocess.run(["git", *args], cwd=cwd, env={"PATH": os.environ["PATH"], "HOME": "/tmp", **GIT_ENV}, check=True,
                   capture_output=True, timeout=60)


def repositories(root: Path) -> None:
    work = Path(tempfile.mkdtemp(prefix="seed-"))
    git("init", "-q", "-b", "main", str(work))
    (work / "README.md").write_text(f"# journey-repo\n\n{MARKER}\n")
    (work / "src").mkdir()
    (work / "src" / "connectors.py").write_text("CONNECTORS = ['slack', 'jira']\n")
    git("add", "-A", cwd=work)
    git("commit", "-q", "-m", "the journey repository", cwd=work)
    bare = root / f"{REPOSITORY}.git"
    bare.parent.mkdir(parents=True, exist_ok=True)
    git("init", "-q", "--bare", "-b", "main", str(bare))
    git("push", "-q", str(bare), "main", cwd=work)


def pki(state: Path) -> tuple[Path, Path]:
    """A test authority and a certificate for ``NAMES``; the authority's certificate is what the worker trusts."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    now = dt.datetime.now(dt.UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "pxh e2e git host test authority")])
    ca = (
        x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(hours=1))
        .not_valid_after(now + dt.timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    cert = (
        x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, NAMES[0])]))
        .issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(hours=1)).not_valid_after(now + dt.timedelta(days=30))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(n) for n in NAMES]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    pem = serialization.Encoding.PEM
    private = Path(tempfile.mkdtemp(prefix="tls-"))
    cert_file, key_file = private / "cert.pem", private / "key.pem"
    cert_file.write_bytes(cert.public_bytes(pem))
    key_file.write_bytes(key.private_bytes(pem, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    published = state / "ca.pem"
    (state / "ca.pem.tmp").write_bytes(ca.public_bytes(pem))
    (state / "ca.pem.tmp").replace(published)
    return cert_file, key_file


class Handler(BaseHTTPRequestHandler):
    root: Path
    requests: Path
    lock = threading.Lock()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return

    def do_GET(self) -> None:
        self._backend(b"")

    def do_POST(self) -> None:
        self._backend(self.rfile.read(int(self.headers.get("Content-Length") or 0)))

    def _backend(self, body: bytes) -> None:
        path, _, query = self.path.partition("?")
        with self.lock, self.requests.open("a") as log:
            log.write(f"{self.command} {path}\n")
        self._alias(path)
        env = {
            "PATH": os.environ["PATH"],
            "GIT_PROJECT_ROOT": str(self.root),
            "GIT_HTTP_EXPORT_ALL": "1",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "REQUEST_METHOD": self.command,
            "PATH_INFO": path,
            "QUERY_STRING": query,
            "CONTENT_TYPE": self.headers.get("Content-Type", ""),
            "CONTENT_LENGTH": str(len(body)),
            "REMOTE_ADDR": self.client_address[0],
        }
        if "Content-Encoding" in self.headers:
            env["HTTP_CONTENT_ENCODING"] = self.headers["Content-Encoding"]
        if "Git-Protocol" in self.headers:
            env["GIT_PROTOCOL"] = self.headers["Git-Protocol"]
        out = subprocess.run(["git", "http-backend"], input=body, env=env, capture_output=True, timeout=120).stdout
        head, sep, payload = out.partition(b"\r\n\r\n")
        if not sep:
            head, _, payload = out.partition(b"\n\n")
        status, headers = 200, []
        for line in head.decode("latin-1").splitlines():
            name, _, value = line.partition(":")
            if name.lower() == "status":
                status = int(value.strip().split(" ", 1)[0])
            elif name:
                headers.append((name.strip(), value.strip()))
        self.send_response(status)
        for name, value in headers:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


    def _alias(self, path: str) -> None:
        """Every ``pxh-e2e/<name>`` is the journey repository, so each journey clones a name of its own and its
        requests are told apart in the log."""
        parts = path.strip("/").split("/")
        if len(parts) < 2 or parts[0] != OWNER or not parts[1].endswith(".git") or "/" in parts[1]:
            return
        if not all(c.isalnum() or c in "-_." for c in parts[1]) or parts[1].startswith("."):
            return
        link = self.root / OWNER / parts[1]
        with self.lock:
            if not link.exists():
                link.symlink_to(self.root / f"{REPOSITORY}.git")


def main() -> None:
    state = Path(sys.argv[1])
    state.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="repos-"))
    repositories(root)
    cert, key = pki(state)
    Handler.root, Handler.requests = root, state / "requests.log"
    Handler.requests.touch()
    server = ThreadingHTTPServer(("0.0.0.0", 443), Handler)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    tls.load_cert_chain(cert, key)
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    (state / "ready").write_text("ok\n")
    server.serve_forever()


if __name__ == "__main__":
    main()
