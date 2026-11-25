"""SIGNIN-1: the real Claude Code binary's login, status and logout against a fake Anthropic, offline.

Two containers on an ``--internal`` Docker network (no route out): the fake server (``fake_anthropic.py``) and the
admin-built Claude Code tools image, which resolves Anthropic's hosts to the fake and trusts a throwaway CA made
here. Nothing reaches a real Anthropic host and no real account exists. ``scenario()`` runs every step the plan
relies on and returns what the binary did; ``python -m tests.unit.harness_engines.claude_code.signin_lab.lab``
rewrites the recorded fixtures from it.
"""

from __future__ import annotations

import datetime
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

if TYPE_CHECKING:
    from collections.abc import Iterator

HERE = Path(__file__).parent
FIXTURES = HERE.parent / "fixtures" / "signin"
# Every host the binary's production OAuth configuration names (SIGNIN-1 read them from the binary).
HOSTS = (
    "api.anthropic.com",
    "claude.ai",
    "claude.com",
    "platform.claude.com",
    "console.anthropic.com",
)
# The run's tool gateway (``launch.tools_mcp_url``'s host), which the fake also serves.
TOOL_GATEWAY = "tool-gateway"
GOOD_CODE = "FAKECODE123"
OWNER_LABEL = "pipeshub.harness.owner"
# The lane that runs it labels its containers as its own (`PXH_HARNESS_OWNER`); only those are ever removed.
OWNER = os.environ.get("PXH_HARNESS_OWNER", "signin")


def _docker(*args: str, check: bool = True, timeout: float = 180) -> str:
    done = subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=timeout
    )
    if check and done.returncode != 0:
        raise RuntimeError(f"docker {args[0]} failed: {done.stderr.strip()}")
    return done.stdout


def _rig_network(name: str) -> None:
    """The lab's ``--internal`` network on a subnet of the rig pool, never the daemon's default pools (a shared daemon
    runs out of them)."""
    import docker

    from tests.support.docker_networks import create_rig_network

    client = docker.from_env()
    try:
        _ = create_rig_network(client, name, labels={OWNER_LABEL: OWNER}, internal=True)
    finally:
        client.close()


def _certs(into: Path) -> None:
    """A throwaway CA and a server certificate for every Anthropic host; the keys never leave ``into``."""
    now = datetime.datetime.now(datetime.UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "pxh-signin-fake-ca")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    cert = (
        x509.CertificateBuilder()
        .subject_name(
            x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "fake-anthropic")])
        )
        .issuer_name(ca_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(h) for h in HOSTS]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    (into / "ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (into / "server.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (into / "server.key").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    for path in into.iterdir():
        path.chmod(0o644)


class Lab:
    def __init__(self, image: str, work: Path) -> None:
        self.image = image
        self.work = work
        tag = uuid.uuid4().hex[:8]
        self.net = f"pxh-signin-lab-{tag}"
        self.fake = f"pxh-signin-fake-{tag}"
        self.client = f"pxh-signin-cc-{tag}"
        self.log = work / "log"
        self._seen = 0

    def start(self) -> None:
        certs = self.work / "certs"
        certs.mkdir()
        self.log.mkdir()
        self.log.chmod(0o777)
        _certs(certs)
        label = ("--label", f"{OWNER_LABEL}={OWNER}")
        _rig_network(self.net)
        _docker(
            "run",
            "-d",
            "--name",
            self.fake,
            *label,
            "--network",
            self.net,
            "--network-alias",
            "fake",
            "--user",
            "0",
            "-v",
            f"{certs}:/certs:ro",
            "-v",
            f"{self.log}:/log",
            "-v",
            f"{HERE / 'fake_anthropic.py'}:/srv/fake.py:ro",
            "--entrypoint",
            "python3",
            self.image,
            "/srv/fake.py",
        )
        ip = _docker(
            "inspect",
            "-f",
            f'{{{{(index .NetworkSettings.Networks "{self.net}").IPAddress}}}}',
            self.fake,
        ).strip()
        hosts = [arg for host in (*HOSTS, TOOL_GATEWAY) for arg in ("--add-host", f"{host}:{ip}")]
        _docker(
            "run",
            "-d",
            "--name",
            self.client,
            *label,
            "--network",
            self.net,
            *hosts,
            "-e",
            "NODE_EXTRA_CA_CERTS=/certs/ca.pem",
            "-e",
            "HOME=/tmp",
            "--tmpfs",
            "/state:uid=1000,gid=1000,mode=0700",
            "-v",
            f"{certs / 'ca.pem'}:/certs/ca.pem:ro",
            "-v",
            f"{HERE / 'drive.py'}:/lab/drive.py:ro",
            "--entrypoint",
            "sleep",
            self.image,
            "infinity",
        )
        self.control(plan="claude_max")

    def close(self) -> None:
        _docker("rm", "-f", self.fake, self.client, check=False)
        _docker("network", "rm", self.net, check=False)

    def control(self, **switches: str) -> None:
        query = "&".join(f"{k}={v}" for k, v in switches.items())
        _docker(
            "exec",
            self.client,
            "python3",
            "-c",
            "import ssl,urllib.request;urllib.request.urlopen('https://api.anthropic.com/control?"
            f"{query}',context=ssl._create_unverified_context()).read()",
        )

    def drive(self, config: str, *args: str) -> dict[str, Any]:
        out = _docker(
            "exec",
            "-e",
            f"CLAUDE_CONFIG_DIR=/state/{config}",
            self.client,
            "python3",
            "/lab/drive.py",
            *args,
            timeout=240,
        )
        return json.loads(out.strip().splitlines()[-1])

    def requests(self) -> list[dict[str, Any]]:
        """The requests the fake received since the last call."""
        path = self.log / "requests.jsonl"
        lines = path.read_text().splitlines() if path.exists() else []
        new, self._seen = lines[self._seen :], len(lines)
        return [json.loads(line) for line in new]


@contextmanager
def lab(image: str) -> Iterator[Lab]:
    work = Path(tempfile.mkdtemp(prefix="pxh-signin-"))
    running = Lab(image, work)
    try:
        running.start()
        yield running
    finally:
        running.close()
        shutil.rmtree(work, ignore_errors=True)


def scenario(image: str) -> dict[str, Any]:
    """Every step SIGNIN relies on, as the binary did it. Requests are reduced to host, method, path and body keys."""
    out: dict[str, Any] = {}
    with lab(image) as run:

        def calls() -> list[dict[str, Any]]:
            return [
                {
                    "host": r["host"],
                    "method": r["method"],
                    "path": r["path"].partition("?")[0],
                    "body": sorted(r["body"]) if isinstance(r["body"], dict) else None,
                    "grant_type": r["body"].get("grant_type")
                    if isinstance(r["body"], dict)
                    else None,
                }
                for r in run.requests()
            ]

        out["status_signed_out"] = run.drive("a", "status")
        out["login"] = run.drive("a", "login", GOOD_CODE)
        out["login_requests"] = calls()
        out["status_signed_in"] = run.drive("a", "status")
        out["prompt_ok"] = run.drive("a", "prompt")
        _ = calls()
        run.control(messages="expired")
        out["prompt_expired"] = run.drive("a", "prompt")
        out["prompt_expired_requests"] = calls()
        run.control(messages="limit")
        out["prompt_limit"] = run.drive("a", "prompt")
        run.control(messages="ok")
        _ = calls()
        out["logout"] = run.drive("a", "logout")
        out["logout_requests"] = calls()
        out["status_after_logout"] = run.drive("a", "status")
        out["login_bad_code"] = run.drive("b", "login", "WRONGCODE")
        out["login_code_without_state"] = run.drive("c", "login", GOOD_CODE, "raw")
        out["prompt_signed_out"] = run.drive("d", "prompt")
        plans: dict[str, Any] = {}
        for plan in ("claude_pro", "claude_max", "claude_team", "claude_enterprise"):
            run.control(plan=plan)
            _ = run.drive(f"plan-{plan}", "login", GOOD_CODE)
            plans[plan] = json.loads(
                run.drive(f"plan-{plan}", "status")["json"]["stdout"]
            )
        out["plans"] = plans
        run.control(plan="claude_max", revoke="fail")
        _ = run.drive("e", "login", GOOD_CODE)
        _ = calls()
        out["logout_revoke_failed"] = run.drive("e", "logout")
        out["logout_revoke_failed_requests"] = calls()
        out["status_after_failed_revoke"] = run.drive("e", "status")
    return out


if __name__ == "__main__":
    import sys

    from tests.unit.harness_engines.claude_code.signin_lab.record import write_fixtures

    write_fixtures(
        scenario(sys.argv[1] if len(sys.argv) > 1 else "pxh-claude-code-tools:2.1.319-ph7")
    )
