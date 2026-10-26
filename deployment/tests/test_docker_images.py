#!/usr/bin/env python3
"""Static checks for the image Dockerfiles, compose files and ignore rules.

These do not build an image. They fail when a change would put Chromium or
the embedding models back into the slim target, drop COPY --link, or leave
Node 20 in the image definitions.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

APP = ROOT / "Dockerfile"
BASE = ROOT / "Dockerfile.base"
DEV = ROOT / "deployment" / "docker-compose" / "hot-reload" / "Dockerfile.dev"
SANDBOX = ROOT / "deployment" / "sandbox" / "Dockerfile"
IGNORE = ROOT / ".dockerignore"
HADOLINT = ROOT / ".hadolint.yaml"

COMPOSE_FILES = (
    ROOT / "deployment" / "docker-compose" / "docker-compose.yml",
    ROOT / "deployment" / "docker-compose" / "docker-compose.build.neo4j.yml",
    ROOT / "deployment" / "docker-compose" / "docker-compose.integration.neo4j.yml",
    ROOT / "deployment" / "docker-compose" / "docker-compose.integration.arango.yml",
    ROOT / "deployment" / "docker-compose" / "beta" / "docker-compose.build.neo4j.slim.yml",
    ROOT / "deployment" / "docker-compose" / "beta" / "docker-compose.build.opensearch.neo4j.yml",
)


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def stage_body(text: str, name: str) -> str:
    """Text of a named stage, up to the next top-level FROM."""
    match = re.search(rf"(?m)^FROM .+ AS {re.escape(name)}\s*$", text)
    if not match:
        fail(f"stage {name} not found")
    rest = text[match.end() :]
    nxt = re.search(r"(?m)^FROM ", rest)
    return rest[: nxt.start()] if nxt else rest


def normalize_requirement(name: str, specifier: str) -> tuple[str, str]:
    canonical = re.sub(r"[-_.]+", "-", name).lower()
    return canonical, ",".join(sorted(specifier.replace(" ", "").split(",")))


def check_uv_lock_matches_pyproject() -> None:
    """The image installs uv.lock with --frozen, which never compares it to pyproject.toml.

    A dependency added to pyproject.toml without re-locking builds fine and then
    fails at import time inside the container. `uv lock --check` catches this in
    CI but needs the network; this compares the lock's recorded inputs offline.
    """
    python_dir = ROOT / "backend" / "python"
    project = tomllib.loads((python_dir / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    lock = tomllib.loads((python_dir / "uv.lock").read_text(encoding="utf-8"))
    root = next((p for p in lock["package"] if p["name"] == project["name"]), None)
    if root is None:
        fail(f"uv.lock has no entry for {project['name']}; run `uv lock` in backend/python")

    locked = {
        normalize_requirement(dep["name"], dep.get("specifier", ""))
        for dep in root["metadata"]["requires-dist"]
        if "marker" not in dep
    }
    for requirement in project["dependencies"]:
        if ";" in requirement:
            continue
        match = re.match(r"^\s*([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?\s*(@.*|[^@]*)$", requirement)
        if not match:
            fail(f"cannot parse pyproject dependency {requirement!r}")
        name, spec = match.group(1), match.group(2).strip()
        if spec.startswith("@"):
            if normalize_requirement(name, "")[0] not in {n for n, _ in locked}:
                fail(f"uv.lock is stale: {name} is not locked; run `uv lock` in backend/python")
            continue
        if normalize_requirement(name, spec) not in locked:
            fail(f"uv.lock is stale for {requirement!r}; run `uv lock` in backend/python")


def main() -> None:
    app = APP.read_text(encoding="utf-8")
    base = BASE.read_text(encoding="utf-8")
    dev = DEV.read_text(encoding="utf-8")
    sandbox = SANDBOX.read_text(encoding="utf-8")
    ignore = IGNORE.read_text(encoding="utf-8")

    for name in ("runtime-slim", "runtime", "nodejs-prod-deps"):
        if f"AS {name}" not in app:
            fail(f"Dockerfile is missing stage {name}")

    slim = stage_body(app, "runtime-slim")
    full = stage_body(app, "runtime")
    if re.search(r"COPY[^\n]*(ms-playwright|huggingface)", slim):
        fail("runtime-slim copies browsers or model weights")
    if "/root/.cache/ms-playwright" not in full or "huggingface" not in full:
        fail("runtime (full) must copy browsers and the model cache")
    if "COPY --link" not in slim:
        fail("runtime-slim copies are missing --link")
    if re.search(r"(?m)^COPY (?!--link)", slim):
        fail("runtime-slim has a COPY without --link")
    if not re.search(r"(?m)^RUN chown 1000:1000 /app /app/backend", slim):
        # process_monitor.sh logs to /app; uid 1000 cannot create files in a root-owned /app.
        fail("runtime-slim must hand /app and /app/backend to uid 1000 after the --link copies")
    if "nodejs-prod-deps" not in app or "node_modules/bun" not in app or "node_modules/@oven" not in app:
        fail("nodejs-prod-deps must remove bun and @oven")
    if "jpeg-recompress-bin" in app or "jpeg-recompress-bin" in dev:
        fail("ARM jpeg-recompress npm hack is back")
    if "REQUIRE_CURRENT_BASE" not in app:
        fail("Dockerfile must be able to refuse a stale python-deps base")
    if "process_monitor.sh" not in app or "cat >" in app and "process_monitor" in app:
        fail("process_monitor.sh must be copied, not embedded")
    if "python-deps-slim" in app or "python-deps-slim" in base:
        fail("python-deps-slim is no longer a base target")

    for label, text in (("Dockerfile", app), ("Dockerfile.base", base), ("Dockerfile.dev", dev), ("sandbox", sandbox)):
        if re.search(r"node:20|setup_20", text):
            fail(f"{label} still installs Node 20")
        if "FROM python:" in text or "FROM node:" in text:
            # Upstream images are pinned on ARG lines, not written as a bare FROM.
            fail(f"{label} has an unpinned FROM python: or FROM node:")

    for text, label in ((app, "Dockerfile"), (base, "Dockerfile.base"), (dev, "Dockerfile.dev"), (sandbox, "sandbox")):
        for arg in ("PYTHON_IMAGE", "NODE_IMAGE", "UV_IMAGE"):
            match = re.search(rf"^ARG {arg}=(\S+)", text, re.M)
            if not match:
                continue
            if "@sha256:" not in match.group(1):
                fail(f"{label} {arg} is not pinned by digest")

    if "uv export --frozen" not in base or "--require-hashes" not in base:
        fail("Dockerfile.base must install from a hashed uv export")
    for label, text in (("Dockerfile", app), ("Dockerfile.base", base)):
        # uv.lock has a git source (etcd3) with no hash; --require-hashes rejects the raw export.
        if re.search(r"--require-hashes -r /tmp/requirements\.txt", text):
            fail(f"{label} passes the raw uv export to --require-hashes; split out git sources first")
        if "--require-hashes" in text and "requirements-git.txt" not in text:
            fail(f"{label} must install git sources separately from the hashed requirements")
    if "playwright install chromium" not in base or "patchright install chromium" not in base:
        fail("Chromium for both Playwright and Patchright is installed in the python-deps base")
    if "bge-large-en-v1.5" not in base or "bge-reranker-base" not in base:
        fail("the models target must bake the BGE embedding and reranker")
    models = stage_body(base, "models")
    deps = stage_body(base, "python-deps")
    if "bge-large" in deps or "bge-reranker" in deps:
        fail("python-deps must not download embedding or reranker weights")
    if "bge-large" not in models:
        fail("models stage must download the embedding model")
    if "SHELL" not in base or "pipefail" not in base:
        fail("Dockerfile.base must set pipefail")

    if "AS runtime-slim" in dev:
        pass
    if "huggingface" in dev:
        fail("Dockerfile.dev must not copy a model cache the python-deps base does not have")

    for needle in ("**/.next/", "frontend/out/", "**/.github/", "**/.cursor/", "**/integration-tests/"):
        if needle not in ignore:
            fail(f".dockerignore is missing {needle}")

    if not HADOLINT.is_file():
        fail(".hadolint.yaml is missing")

    nvmrc = ROOT / ".nvmrc"
    if not nvmrc.is_file() or nvmrc.read_text(encoding="utf-8").strip() != "22":
        fail(".nvmrc must contain 22")

    for compose in COMPOSE_FILES:
        text = compose.read_text(encoding="utf-8")
        if "PIPESHUB_BUILD_TARGET" not in text:
            fail(f"{compose.relative_to(ROOT)} must set the image target from PIPESHUB_BUILD_TARGET")
        if "MODELS_IMAGE" not in text:
            fail(f"{compose.relative_to(ROOT)} must pass MODELS_IMAGE so an empty override cannot wipe the default")
        if "python-deps-slim" in text:
            fail(f"{compose.relative_to(ROOT)} still references python-deps-slim")

    for compose in sorted((ROOT / "deployment" / "docker-compose").rglob("docker-compose*.yml")):
        text = compose.read_text(encoding="utf-8")
        for probe in re.finditer(r'"mongosh", "--eval", "db\.adminCommand\(\'ping\'\)"\]\n((?:\s+.+\n)+)', text):
            block = probe.group(1)
            timeout = re.search(r"timeout: (\d+)s", block)
            # mongosh startup alone takes 1-4s on a loaded host; app depends_on mongodb being healthy.
            if not timeout or int(timeout.group(1)) < 10 or "start_period:" not in block:
                fail(f"{compose.relative_to(ROOT)} mongodb healthcheck needs timeout >= 10s and a start_period")

    check_uv_lock_matches_pyproject()

    workflow_dir = ROOT / ".github" / "workflows"
    for path in workflow_dir.glob("*.yml"):
        text = path.read_text(encoding="utf-8")
        if re.search(r"node-version:\s*['\"]?20", text) or "node-version: [20]" in text:
            fail(f"{path.name} still requests Node 20")

    print("test_docker_images: ok")


if __name__ == "__main__":
    main()
