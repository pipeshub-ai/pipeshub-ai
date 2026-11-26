"""The sandbox contract job checks the built sandbox image's Node against the major its Dockerfile copies in.

The check pinned v22 while the Dockerfile moved to node:24-slim, so it failed as soon as the job checked the image it
had just built rather than a stale one. The workflow is parsed and its extraction run; the image is not.
"""

from __future__ import annotations

import re
import subprocess

import yaml

from tests.cohesion.support import REPO_ROOT

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "agent-harness-infrastructure.yml"
DOCKERFILE = REPO_ROOT / "deployment" / "sandbox" / "Dockerfile"
STEP = "Verify the sandbox Node dependency boundary"


def _step_script() -> str:
    steps = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["sandbox-contract"]["steps"]
    (step,) = [s for s in steps if s.get("name") == STEP]
    return step["run"]


def test_the_node_check_reads_the_major_from_the_dockerfile() -> None:
    script = _step_script()
    assert not re.search(r"\^v\d+", script), "no Node major is written into the check"
    extract = next(line for line in script.splitlines() if line.startswith("node_major="))
    done = subprocess.run(["bash", "-c", f'{extract}\nprintf "%s" "$node_major"'], cwd=REPO_ROOT,
                          capture_output=True, text=True, check=True)
    (pinned,) = re.findall(r"^FROM node:(\d+)-slim@", DOCKERFILE.read_text(encoding="utf-8"), re.M)
    assert done.stdout == pinned
    assert 'grep "^v$NODE_MAJOR\\."' in script
