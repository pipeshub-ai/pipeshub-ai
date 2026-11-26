"""The ops exporter must not change what the push telemetry sends."""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

PY = Path(__file__).resolve().parents[3]

PROBE = textwrap.dedent(
    """
    import sys
    from app.telemetry.backend import PrometheusBackend
    from app.telemetry import flag
    # A build whose recording waits for an admin's consent (its default follows the edition) gets it here.
    if hasattr(flag, "set_collection_consent"):
        flag.set_collection_consent(True)
    if sys.argv[1] == "on":
        from app.telemetry import ops_exporter  # noqa: F401  (imported; a collector is registered below)
        import tempfile
        e = ops_exporter.OpsExporter(service="indexing", run_dir=tempfile.mkdtemp(), port=19299, source=lambda: ({}, {}))
        e.writer.write()
    b = PrometheusBackend()
    c = b.counter("t_total_x", "h", ["k"])
    g = b.gauge("t_gauge_x", "h", ["k"])
    out = []
    c.inc("a"); g.set("a", value=3); out.append(b.serialize())
    b.reset(); c.inc("a"); out.append(b.serialize())
    sys.stdout.write("\\n--\\n".join(out))
    """
)


def _run(mode: str, extra_env: dict[str, str] | None = None) -> str:
    env = {k: v for k, v in os.environ.items() if k != "PROMETHEUS_MULTIPROC_DIR"}
    env.update(extra_env or {})
    done = subprocess.run([sys.executable, "-c", PROBE, mode], cwd=PY, env=env, capture_output=True, text=True, timeout=60, check=True)
    return "\n".join(line for line in done.stdout.splitlines() if "_created" not in line)  # creation times vary


def test_serialize_reset_serialize_is_identical_with_the_exporter_on_and_off() -> None:
    off, on = _run("off"), _run("on")
    assert on == off
    assert "pipeshub_governor" not in on and "pipeshub_cgroup" not in on
    assert 't_total_x_total{k="a"} 1.0' in on


def test_negative_control_multiprocess_dir_changes_the_second_window(tmp_path: Path) -> None:
    """Setting PROMETHEUS_MULTIPROC_DIR is what the design rejects: it changes what the push sends."""
    baseline = _run("off")
    changed = _run("off", {"PROMETHEUS_MULTIPROC_DIR": str(tmp_path)})
    assert changed != baseline
