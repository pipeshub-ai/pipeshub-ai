"""A long-lived e2e stack (the human-test stack) survives a rebuild onto code that needs a credential it lacks.

R38-IDENT added EGRESS_PROXY_COMPONENT_KEY, SANDBOX_BROKER_COMPONENT_KEY and DOCKER_PROXY_TOKEN to the env file that
`run.sh` writes at creation; a stack made earlier predates them and `rebuild` never wrote them, so a refresh would start
services that refuse to run. `rebuild` now calls `backfill_env`. These tests keep every generated value of the env file
in exactly one of its two lists, so the next new credential cannot be forgotten, and run the function on a stale file.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
RUN_SH = ROOT / "deployment" / "e2e" / "run.sh"


def _env_template() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    match = re.search(r'cat >"\$ENV_FILE" <<EOF\n(.*?)\nEOF\n', text, re.S)
    assert match, "run.sh no longer writes its env file from one heredoc"
    return match.group(1)


def _array(name: str) -> list[str]:
    text = RUN_SH.read_text(encoding="utf-8")
    match = re.search(rf"^{name}=\((.*?)\)", text, re.S | re.M)
    assert match, f"run.sh lost {name}"
    return match.group(1).split()


def test_every_generated_value_is_backfilled_or_named_as_not() -> None:
    generated = set(re.findall(r"^([A-Z0-9_]+)=.*\$\(rand ", _env_template(), re.M))
    backfilled = {spec.split(":", 1)[0] for spec in _array("BACKFILLED_KEYS")}
    not_backfilled = set(_array("NOT_BACKFILLED_KEYS"))
    assert not backfilled & not_backfilled, backfilled & not_backfilled
    assert generated == backfilled | not_backfilled, {
        "unlisted": sorted(generated - backfilled - not_backfilled),
        "listed but not generated": sorted((backfilled | not_backfilled) - generated),
    }


def test_backfill_adds_missing_keys_and_keeps_set_ones(tmp_path: Path) -> None:
    env = tmp_path / "stack.env"
    env.write_text("SECRET_KEY=keep-me\nJWT_SECRET=keep-too\nNEO4J_PASSWORD=store\n", encoding="utf-8")
    text = RUN_SH.read_text(encoding="utf-8")
    pieces = [
        re.search(r"^rand\(\) \{.*?\}$", text, re.M).group(0),
        re.search(r"^set_envv\(\) \{.*?^\}$", text, re.M | re.S).group(0),
        re.search(r"^BACKFILLED_KEYS=\(.*?\)$", text, re.M | re.S).group(0),
        re.search(r"^backfill_env\(\) \{.*?^\}$", text, re.M | re.S).group(0),
    ]
    script = "\n".join(["set -euo pipefail", f'ENV_FILE="{env}"', *pieces, "backfill_env", "backfill_env"])
    subprocess.run(["bash", "-c", script], check=True)
    values = dict(line.split("=", 1) for line in env.read_text(encoding="utf-8").splitlines() if "=" in line)
    assert values["SECRET_KEY"] == "keep-me" and values["JWT_SECRET"] == "keep-too"
    assert values["NEO4J_PASSWORD"] == "store"
    for spec in _array("BACKFILLED_KEYS"):
        name, length = spec.split(":", 1)
        if name == "JWT_SECRET":
            continue
        assert re.fullmatch(rf"[0-9a-f]{{{int(length) * 2}}}", values[name]), name
    assert values["DOCKER_GID"].isdigit()
    assert sum(line.startswith("DOCKER_PROXY_TOKEN=") for line in env.read_text().splitlines()) == 1


def test_rebuild_waits_for_the_sandbox_plane_after_the_app() -> None:
    """The proxy and broker log in as Redis ACL users the app creates at start (ADR-KEY-02): waiting for them before
    the app is recreated deadlocks a stack made before those users existed."""
    text = RUN_SH.read_text(encoding="utf-8")
    body = re.search(r"^rebuild\(\) \{.*?^\}$", text, re.M | re.S).group(0)
    assert body.index("backfill_env") < body.index("compose up")
    app = body.index("wait_app")
    for svc in ("egress-proxy", "sandbox-broker"):
        waits = [m.start() for m in re.finditer(rf"wait_compose_healthy {svc}\b", body)]
        assert waits and all(at > app for at in waits), svc
    loop = re.search(r'for svc in "\$\{services\[@\]\}"; do(.*?)done', body, re.S).group(1)
    assert "egress-proxy|sandbox-broker) continue" in loop


def test_rebuild_gives_a_network_added_since_its_own_subnet(tmp_path: Path) -> None:
    """R43-SOCK added sandbox_docker: a stack made before it has a networks.yml without it, and Docker's default pools
    are exhausted on a shared daemon. `rebuild` assigns a subnet to a network the stack has not created yet and leaves
    every network it was given alone; a stack's first `assign_subnets` call is unchanged."""
    text = RUN_SH.read_text(encoding="utf-8")
    body = re.search(r"^rebuild\(\) \{.*?^\}$", text, re.M | re.S).group(0)
    assert body.index("assign_subnets --new") < body.index("compose up")
    fn = re.search(r"^assign_subnets\(\) \{.*?^\}$", text, re.M | re.S).group(0)
    (tmp_path / "networks.yml").write_text("networks:\n  pipeshub:\n    ipam:\n      config:\n        - subnet: 10.216.5.0/26\n")
    stubs = ('compose() { printf "pipeshub\\nsandbox_docker\\n"; }\n'
             'docker() { case "$1 $2" in "network ls") ;; "network inspect") [[ "$3" == pxh-old_pipeshub ]] ;; esac; }\n')
    script = f"{stubs}\n{fn}\nWORK={tmp_path}\nPROJECT=pxh-old\nassign_subnets\nassign_subnets --new\nassign_subnets --new\n"
    subprocess.run(["bash", "-c", script], check=True, timeout=60)
    written = (tmp_path / "networks.yml").read_text()
    assert written.startswith("networks:\n  pipeshub:\n    ipam:\n      config:\n        - subnet: 10.216.5.0/26\n")
    assert written.count("  sandbox_docker:\n") == 1 and written.count("subnet:") == 2
