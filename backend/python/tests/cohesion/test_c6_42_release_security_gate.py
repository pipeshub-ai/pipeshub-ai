"""C6.42: a release is published only after the security suite and the canary scans pass on the release's commit.

One reusable workflow (``harness-security-gate.yml``) runs the gate's stages, and one script
(``scripts/security_gate.py``) says what each stage runs and which skips it refuses: the supply-chain check, the
security suite, the QA14 canary scans, C-13 (a sandbox reaches only the gateways), the Docker lane and the engine
lanes. The pull-request lane and the release both call the workflow and the workflow only calls the script, so the
gate a release must pass is the one every harness change runs and the one a maintainer runs before tagging, never a
copy that drifts. The engine stage fails when a real-lane case skipped for want of an image the gate built
(``scripts/engines/release_images.py``, whose plan and check are tested here on the real allowlist and on reports).
The gate's verdicts are tested on JUnit reports a fake runner writes (SG-1..SG-7); the workflows are parsed, not run.
The runsc half is deferred to the end with the rest of gVisor.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from functools import cache
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.cohesion.support import REPO_ROOT

WORKFLOWS = REPO_ROOT / ".github" / "workflows"
GATE = "./.github/workflows/harness-security-gate.yml"
C13 = "tests/cohesion/test_c13_sandbox_reaches_only_gateways.py"
PYTHON = REPO_ROOT / "backend" / "python"
ALLOWLIST = PYTHON / "harness_engines" / "allowlist.json"


def _module(name: str, path: Path) -> Any:  # noqa: ANN401 - a module loaded by path
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, module)  # dataclasses look their module up while the class is built
    spec.loader.exec_module(module)
    return module


@cache
def _release_images() -> Any:  # noqa: ANN401
    return _module("release_images", REPO_ROOT / "scripts" / "engines" / "release_images.py")


@cache
def _gate() -> Any:  # noqa: ANN401
    return _module("security_gate", REPO_ROOT / "scripts" / "security_gate.py")


def _stage(name: str) -> Any:  # noqa: ANN401
    (stage,) = [s for s in _gate().stages(REPO_ROOT) if s.name == name]
    return stage


def _load(name: str) -> dict[Any, Any]:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _runs(job: dict[str, Any]) -> list[dict[str, Any]]:
    return [step for step in job.get("steps", []) if "run" in step]


def test_c6_42_promote_waits_for_the_security_gate() -> None:
    jobs = _load("release-publish.yml")["jobs"]
    assert jobs["security"]["uses"] == GATE
    needs = jobs["promote"]["needs"]
    assert "security" in needs and "verify" in needs, "no tag moves before the security gate and the smoke pass"


def test_c6_42_the_pull_request_lane_runs_the_same_gate() -> None:
    jobs = _load("agent-harness-infrastructure.yml")["jobs"]
    assert jobs["harness-docker-lane"]["uses"] == GATE, "one definition of the gate, called from both"


def test_c6_42_the_gate_is_callable_and_each_job_runs_the_scripts_stages() -> None:
    gate = _load("harness-security-gate.yml")
    assert "workflow_call" in gate[True]  # YAML 1.1 reads the `on` key as true
    called = [
        step["run"].split("security_gate.py run ", 1)[1].split()
        for job in gate["jobs"].values()
        for step in _runs(job)
        if "security_gate.py run " in step["run"]
    ]
    names = [s.name for s in _gate().stages(REPO_ROOT)]
    assert sorted(n for c in called for n in c) == sorted(names), "every stage runs once in CI, and nothing else"
    for job in gate["jobs"].values():
        for step in _runs(job):
            assert "pytest" not in step["run"], f"{step.get('name')}: the gate's cases are the script's, not a copy"


def test_c6_42_the_docker_stages_cannot_skip_for_want_of_docker() -> None:
    for name in ("c13", "docker-lane", "engines"):
        stage = _stage(name)
        assert stage.docker and stage.env["PXH_REQUIRE_DOCKER"] == "1", f"{name}: no daemon or image must read red"
    assert C13 in _stage("c13").pytest and _stage("c13").allow_skips == (), "C-13 was skipped once; it may not be"
    assert f"--deselect={C13}" in _stage("docker-lane").pytest
    assert "tests/unit/harness/sandbox_broker/test_engine_tools_image_docker.py" in _stage("engines").pytest, (
        "the tools-layer licence scans (IT-13) run in the gate"
    )
    assert _stage("engines").image_check


def test_c6_42_the_gvisor_stage_runs_the_security_probes_on_runsc_and_cannot_pass_without_it() -> None:
    """SG-9 (R31-GVISO): the escape, exhaustion and internal-reach probes' gVisor lane, with runsc required; the
    Docker lane's job installs gVisor before it."""
    stage = _stage("gvisor")
    assert stage.docker and stage.env["PXH_REQUIRE_DOCKER"] == "1" and stage.env["PXH_REQUIRE_GVISOR"] == "1"
    assert ("-k", "gvisor") == stage.pytest[2:4]
    for name in ("test_sec_iso_escape_docker", "test_sec_iso_exhaustion_docker", "test_egress_security_docker"):
        assert f"tests/unit/harness/sandbox_broker/{name}.py" in stage.pytest
    assert C13 in stage.pytest and C13 in _stage("c13").pytest and "not gvisor" in _stage("c13").pytest
    assert stage.allow_skips == ("is not built",)
    runs = [step.get("run", "") for step in _load("harness-security-gate.yml")["jobs"]["docker-lane"]["steps"]]
    install = next(i for i, r in enumerate(runs) if "runsc install" in r)
    assert install < next(i for i, r in enumerate(runs) if r.endswith("run gvisor"))


def test_c6_42_the_gate_runs_every_qa14_canary_scan_and_none_of_them_may_skip() -> None:
    scans = sorted(p.relative_to(PYTHON).as_posix() for p in (PYTHON / "tests").rglob("test_qa14_*.py"))
    assert len(scans) >= 6, "the QA14 canary kit and its scans"
    canary = _stage("canary")
    assert sorted(canary.pytest) == scans
    assert canary.allow_skips == (), "the activity scan skips without its Redis; the gate refuses that"
    security = _stage("security")
    assert all(f"--deselect={scan}" in security.pytest for scan in scans), "each scan runs once, in its stage"


def test_c6_42_the_canary_job_has_the_redis_its_activity_scan_reads() -> None:
    canary = _load("harness-security-gate.yml")["jobs"]["canary"]
    assert canary["services"]["redis"]["ports"] == ["6379:6379"]
    (step,) = [s for s in _runs(canary) if s["run"].endswith("run canary")]
    assert step["env"]["REDIS_IT_URL"] == "redis://127.0.0.1:6379"


def test_c6_42_nothing_in_the_gate_or_the_publish_path_can_pass_by_being_skipped_or_forgiven() -> None:
    gate = _load("harness-security-gate.yml")["jobs"]
    release = _load("release-publish.yml")["jobs"]
    for name, job in [*gate.items(), ("security", release["security"]), ("promote", release["promote"])]:
        assert "continue-on-error" not in job, f"{name}: a failing gate job would still let the release through"
        for step in job.get("steps", []):
            assert not step.get("continue-on-error") or step.get("name") == "Remove candidate tags", (name, step.get("name"))
    # `if: always()` on promote would publish after a failed or skipped security job.
    assert "if" not in release["promote"] and "if" not in release["security"]


def test_c6_42_the_gate_builds_the_engine_images_before_their_lane() -> None:
    steps = _load("harness-security-gate.yml")["jobs"]["engine-images"]["steps"]
    runs = [step.get("run", "") for step in steps]
    build = next(i for i, r in enumerate(runs) if "release_images.py build" in r)
    lane = next(i for i, r in enumerate(runs) if "security_gate.py run engines" in r)
    assert build < lane


def test_c6_42_the_plan_builds_every_redistributable_engine_and_its_engine_only_image_never_an_admin_one() -> None:
    images = _release_images()
    allowlist = json.loads(ALLOWLIST.read_text(encoding="utf-8"))
    builds = images.plan(allowlist)
    kinds = {b.kind for b in builds}
    defaults = {e["kind"]: e for e in reversed(allowlist["engines"])}
    for kind, entry in defaults.items():
        if entry.get("supply") == "admin":
            assert kind not in kinds, f"{kind} is admin-built: no runner may build it"
        elif (images.IMAGES / kind.replace("_", "-") / "Dockerfile").is_file():
            assert kind in kinds, f"{kind} is redistributable and its cases would skip in the release gate"
            assert entry["image"] in {b.tag for b in builds}
    assert "acp-opencode" not in kinds, "it runs opencode's image, which is built once"
    engine_only = [b for b in builds if b.variant == "engine"]
    assert engine_only and all(b.command()[-2:] == ["--variant", "engine"] for b in engine_only)
    assert all(b.command()[:2] == ["sh", str(images.IMAGES / "engine-build.sh")] for b in builds)


def _report(tmp_path: Path, *skips: str, passed: int = 3) -> Path:
    cases = "".join(f'<testcase classname="t" name="p{i}"/>' for i in range(passed))
    cases += "".join(f'<testcase classname="t" name="s{i}"><skipped message="{m}"/></testcase>' for i, m in enumerate(skips))
    path = tmp_path / "r.xml"
    path.write_text(f"<testsuites><testsuite>{cases}</testsuite></testsuites>", encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "reason",
    [
        "needs Docker, pxh-opencode-tools:1.18.34-ph7 and python:3.12-slim: ImageNotFound",
        "needs Docker (deployment/sandbox-images/codex/build.sh)",
        "needs Docker and the acp-gemini image: NotFound",
    ],
)
def test_c6_42_a_case_skipped_for_want_of_a_built_image_fails_the_gate(tmp_path: Path, reason: str) -> None:
    images = _release_images()
    builds = images.plan(json.loads(ALLOWLIST.read_text(encoding="utf-8")))
    report = _report(tmp_path, reason.replace("1.18.34-ph7", builds[0].tag.split(":", 1)[1]))
    assert images.hidden_cases(builds, [report])
    assert images.main(["check", str(report)]) == 1


def test_c6_42_other_skips_pass_and_an_empty_lane_does_not(tmp_path: Path) -> None:
    images = _release_images()
    allowed = _report(
        tmp_path,
        "needs Docker (deployment/sandbox-images/claude-code/build.sh --binary PATH)",
        "live lane: set HARNESS_LIVE_ENV_FILE",
    )
    assert images.main(["check", str(allowed), "--min-tests", "5"]) == 0
    assert images.main(["check", str(allowed), "--min-tests", "6"]) == 1, "fewer cases than the lane holds ran"


# --- the gate's verdicts (SG-1..SG-8): a fake runner writes the JUnit report pytest would --------------------


def _xml(passed: int = 0, failed: int = 0, skips: tuple[str, ...] = ()) -> str:
    cases = "".join(f'<testcase classname="t" name="p{i}"/>' for i in range(passed))
    cases += "".join(f'<testcase classname="t" name="f{i}"><failure message="boom"/></testcase>' for i in range(failed))
    cases += "".join(f'<testcase classname="t" name="s{i}"><skipped message="{m}"/></testcase>' for i, m in enumerate(skips))
    return f"<testsuites><testsuite>{cases}</testsuite></testsuites>"


class FakeRunner:
    """Answers each stage as a scenario says: (exit code, report XML or None for a pytest that wrote none)."""

    def __init__(self, scenarios: dict[str, tuple[int, str | None]]) -> None:
        self.scenarios = scenarios
        self.ran: list[str] = []

    def _stage_of(self, argv: list[str]) -> str:
        for stage in _gate().stages(REPO_ROOT):
            tail = list(stage.pytest or stage.command[1:])
            if argv[-len(tail):] == tail:
                return stage.name
        raise AssertionError(argv)

    def __call__(self, argv: list[str], cwd: Path, env: dict[str, str]) -> int:
        name = self._stage_of(list(argv))
        self.ran.append(name)
        code, xml = self.scenarios.get(name, (0, _xml(passed=_stage(name).min_tests)))
        report = next((a.split("=", 1)[1] for a in argv if a.startswith("--junitxml=")), None)
        if report is not None and xml is not None:
            Path(report).write_text(xml, encoding="utf-8")
        return code


def _verdict(name: str, code: int, xml: str | None) -> Any:  # noqa: ANN401
    return _gate().run_stage(_stage(name), REPO_ROOT, FakeRunner({name: (code, xml)}))


def test_sg_1_a_failed_case_blocks_the_release() -> None:
    result = _verdict("security", 1, _xml(passed=150, failed=1))
    assert not result.ok and any("FAILED" in f and "boom" in f for f in result.findings)


def test_sg_2_a_skip_blocks_a_stage_that_allows_none_and_only_the_named_cell_may_skip() -> None:
    assert not _verdict("canary", 0, _xml(passed=20, skips=("needs REDIS_IT_URL",))).ok
    assert not _verdict("c13", 0, _xml(passed=3, skips=("needs the Node workspace",))).ok
    assert _verdict("security", 0, _xml(passed=150, skips=("inj-08-plan-execute-web is not a durable run",))).ok
    refused = _verdict("security", 0, _xml(passed=150, skips=("needs Docker",)))
    assert refused.findings == ["SKIPPED t::s0: needs Docker"]


def test_sg_3_an_empty_or_short_run_and_a_missing_report_are_not_a_pass() -> None:
    assert _verdict("canary", 5, _xml()).findings == ["only 0 cases ran; the stage holds at least 10"]
    assert not _verdict("security", 0, _xml(passed=99)).ok, "fewer cases than the suite holds: some were lost"
    assert _verdict("canary", 3, None).findings == ["pytest wrote no report (exit 3)"]


def test_sg_4_a_pytest_failure_outside_any_case_blocks_it() -> None:
    assert not _verdict("canary", 1, _xml(passed=20)).ok, "a collection error or a fixture failure"
    assert not _verdict("canary", 2, _xml(passed=20)).ok, "interrupted"
    assert _verdict("canary", 0, _xml(passed=20)).ok


def test_sg_5_a_supply_chain_finding_blocks_the_release() -> None:
    assert not _verdict("supply-chain", 1, None).ok
    assert _verdict("supply-chain", 0, None).ok


def test_sg_6_run_all_runs_every_stage_after_a_failure_and_names_what_blocks(tmp_path: Path) -> None:
    gate = _gate()
    runner = FakeRunner({"supply-chain": (1, None), "canary": (0, _xml(passed=20, skips=("needs REDIS_IT_URL",)))})
    out = tmp_path / "out.txt"
    with out.open("w", encoding="utf-8") as handle:
        assert gate.run(["all"], REPO_ROOT, runner, handle) == 1
    assert runner.ran == [s.name for s in gate.stages(REPO_ROOT)], "every stage ran, in order, whatever came first"
    text = out.read_text(encoding="utf-8")
    assert "FAIL supply-chain" in text and "FAIL canary" in text and "PASS c13" in text
    assert "blocked by supply-chain, canary" in text
    with out.open("w", encoding="utf-8") as handle:
        assert gate.run(["all"], REPO_ROOT, FakeRunner({}), handle) == 0
        assert gate.run(["nope"], REPO_ROOT, FakeRunner({}), handle) == 2


def test_sg_7_a_new_canary_scan_joins_the_gate_without_an_edit(tmp_path: Path) -> None:
    tests = tmp_path / "backend" / "python" / "tests" / "unit" / "new"
    tests.mkdir(parents=True)
    (tests / "test_qa14_new_surface.py").write_text("", encoding="utf-8")
    (stage,) = [s for s in _gate().stages(tmp_path) if s.name == "canary"]
    assert stage.pytest == ("tests/unit/new/test_qa14_new_surface.py",)


def test_sg_8_the_engine_stage_fails_on_a_case_skipped_for_want_of_its_built_image() -> None:
    images = _release_images()
    tag = images.plan(json.loads(ALLOWLIST.read_text(encoding="utf-8")))[0].tag
    hidden = _verdict("engines", 0, _xml(passed=30, skips=(f"needs Docker, {tag}: ImageNotFound",)))
    assert any("FOR WANT OF A BUILT IMAGE" in f for f in hidden.findings)
    assert _verdict("engines", 0, _xml(passed=30, skips=("live lane: set HARNESS_LIVE_ENV_FILE",))).ok


def _step_index(steps: list[dict[str, Any]], needle: str) -> int:
    return next(i for i, step in enumerate(steps) if needle in step.get("run", ""))


@pytest.mark.parametrize(("job", "stage"), [("docker-lane", "docker-lane"), ("engine-images", "engines")])
def test_c6_42_each_docker_job_builds_the_sandbox_image_its_stage_names(job: str, stage: str) -> None:
    """The tool matrix built its MCP runner FROM a tag no job made (pxh-sandbox:pr1), so every matrix case failed
    at setup: each job builds the image under the gate's one name, and its stage hands that name to the cases."""
    steps = _load("harness-security-gate.yml")["jobs"][job]["steps"]
    build = _step_index(steps, "--profile sandbox build sandbox-image")
    assert steps[build]["env"]["SANDBOX_DOCKER_IMAGE"] == _gate().SANDBOX_IMAGE
    assert build < _step_index(steps, f"security_gate.py run {stage}")
    assert _stage(stage).env["SANDBOX_TEST_IMAGE"] == _gate().SANDBOX_IMAGE


def test_c6_42_the_engine_job_installs_the_node_workspace_its_durable_lanes_start() -> None:
    """The durable lanes run Node's journal server with ts-node from backend/nodejs/apps; without the workspace npx
    fetched a bare ts-node that could not load TypeScript, and every durable case failed to start."""
    steps = _load("harness-security-gate.yml")["jobs"]["engine-images"]["steps"]
    node = next(i for i, s in enumerate(steps) if str(s.get("uses", "")).startswith("actions/setup-node@"))
    assert steps[node]["with"]["node-version"] == "22"
    install = next(i for i, s in enumerate(steps) if s.get("working-directory") == "backend/nodejs/apps")
    assert steps[install]["run"].startswith("npm ci")
    assert node < install < _step_index(steps, "security_gate.py run engines")


def test_c6_42_an_admin_built_engines_scan_skip_names_its_own_build_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """IT-13's Claude Code case named opencode's build script when its image was absent, so the gate read the
    allowed skip of an admin-built image as one for want of a built image."""
    from tests.unit.harness.sandbox_broker import test_engine_tools_image_docker as it13

    monkeypatch.setenv("DOCKER_HOST", "unix:///nonexistent")
    allowlist = json.loads(ALLOWLIST.read_text(encoding="utf-8"))
    claude = next(e for e in allowlist["engines"] if e["kind"] == "claude_code")["image"]
    present, reason = it13._image_present(claude, "claude-code")  # pyright: ignore[reportPrivateUsage]
    assert not present and "sandbox-images/claude-code/build.sh" in reason
    images = _release_images()
    assert images.main(["check", str(_report(tmp_path, reason, passed=1)), "--min-tests", "2"]) == 0
    gemini = next(e for e in allowlist["engines"] if e["kind"] == "acp-gemini")["image"]
    _, reason = it13._image_present(gemini, "acp-gemini")  # pyright: ignore[reportPrivateUsage]
    assert images.main(["check", str(_report(tmp_path, reason, passed=1)), "--min-tests", "2"]) == 1
