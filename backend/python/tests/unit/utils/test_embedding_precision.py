"""Which weight precision the embedding server runs: request, probe, saved decision."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from app.utils import embedding_precision as ep
from app.utils.embedding_precision import (
    FP32,
    INT8,
    PrecisionSelector,
    ProbeResult,
    requested_precision,
    run_probe,
)

SMALL = 8 * 1024**3
LARGE = 32 * 1024**3
PASS = ProbeResult("pass", "ok")
FAIL = ProbeResult("fail", "bad")
SLOW = ProbeResult("inconclusive", "timeout")


class _Probe:
    def __init__(self, *results: ProbeResult) -> None:
        self.results = list(results)
        self.calls = 0

    def __call__(self) -> ProbeResult:
        self.calls += 1
        return self.results.pop(0) if len(self.results) > 1 else self.results[0]


def _selector(tmp_path: Path, probe, *, request="int8", memtotal=SMALL, version="1") -> PrecisionSelector:
    return PrecisionSelector(
        state_path=tmp_path / "embedding_precision.json",
        quantizer_version=version, request=request, memtotal=memtotal, probe=probe,
    )


def _saved(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "embedding_precision.json").read_text())


class TestRequest:
    @pytest.mark.parametrize("raw", [None, "", "  ", "auto", "AUTO"])
    def test_unset_or_empty_is_auto(self, raw, monkeypatch) -> None:
        monkeypatch.delenv(ep.PRECISION_ENV, raising=False)
        assert requested_precision(raw) == "auto"

    @pytest.mark.parametrize("raw", ["int8", "fp32", " INT8 "])
    def test_explicit_values(self, raw) -> None:
        assert requested_precision(raw) == raw.strip().lower()

    def test_unknown_value_is_refused(self) -> None:
        with pytest.raises(ValueError, match="EMBEDDING_SERVER_PRECISION"):
            requested_precision("int4")

    def test_reads_the_environment(self, monkeypatch) -> None:
        monkeypatch.setenv(ep.PRECISION_ENV, "fp32")
        assert requested_precision() == "fp32"


class TestAuto:
    def test_auto_is_fp32_on_every_host_and_never_probes(self, tmp_path) -> None:
        probe = _Probe(PASS)
        for memtotal in (1024**3, SMALL, LARGE, None):
            decision = _selector(tmp_path, probe, request="auto", memtotal=memtotal).decide()
            assert decision.precision == FP32
        assert probe.calls == 0
        assert not (tmp_path / "embedding_precision.json").exists()

    def test_the_memory_rule_is_one_constant_away(self, tmp_path) -> None:
        with patch.object(ep, "AUTO_INT8_BELOW_MEMTOTAL_BYTES", 12 * 1024**3):
            assert _selector(tmp_path, _Probe(PASS), request="auto", memtotal=SMALL).decide().precision == INT8
            big = _Probe(PASS)
            assert _selector(tmp_path / "x", big, request="auto", memtotal=LARGE).decide().precision == FP32
            assert big.calls == 0

    def test_enabled_auto_falls_back_when_the_probe_fails(self, tmp_path) -> None:
        with patch.object(ep, "AUTO_INT8_BELOW_MEMTOTAL_BYTES", 12 * 1024**3):
            decision = _selector(tmp_path, _Probe(FAIL), request="auto").decide()
        assert decision.precision == FP32 and "bad" in decision.reason

    def test_the_shipped_threshold_enables_nothing(self) -> None:
        assert ep.AUTO_INT8_BELOW_MEMTOTAL_BYTES == 0


class TestExplicit:
    def test_fp32_wins_and_skips_the_probe(self, tmp_path) -> None:
        probe = _Probe(PASS)
        decision = _selector(tmp_path, probe, request="fp32").decide()
        assert decision.precision == FP32 and "fp32" in decision.reason and probe.calls == 0

    def test_int8_with_a_passing_probe(self, tmp_path) -> None:
        decision = _selector(tmp_path, _Probe(PASS)).decide()
        assert decision.precision == INT8 and decision.quantizer_version == "1"

    def test_int8_on_a_large_host_is_honoured(self, tmp_path) -> None:
        assert _selector(tmp_path, _Probe(PASS), memtotal=LARGE).decide().precision == INT8

    def test_a_failed_probe_means_fp32_with_the_reason(self, tmp_path) -> None:
        decision = _selector(tmp_path, _Probe(FAIL)).decide()
        assert decision.precision == FP32 and "probe fail: bad" in decision.reason

    def test_only_cpu_uses_int8_weights(self, tmp_path) -> None:
        probe = _Probe(PASS)
        decision = _selector(tmp_path, probe).decide("cuda")
        assert decision.precision == FP32 and probe.calls == 0

    def test_the_decision_is_made_once(self, tmp_path) -> None:
        probe = _Probe(PASS)
        selector = _selector(tmp_path, probe)
        assert selector.decide() is selector.decide() and probe.calls == 1


class TestInconclusive:
    def test_timeout_twice_is_fp32_for_this_boot_and_not_saved(self, tmp_path) -> None:
        probe = _Probe(SLOW)
        decision = _selector(tmp_path, probe).decide()
        assert decision.precision == FP32 and probe.calls == 2
        assert not (tmp_path / "embedding_precision.json").exists()

    def test_one_timeout_is_retried(self, tmp_path) -> None:
        probe = _Probe(SLOW, PASS)
        assert _selector(tmp_path, probe).decide().precision == INT8 and probe.calls == 2

    def test_next_boot_probes_again_after_a_timeout(self, tmp_path) -> None:
        _selector(tmp_path, _Probe(SLOW)).decide()
        probe = _Probe(PASS)
        assert _selector(tmp_path, probe).decide().precision == INT8 and probe.calls == 1


class TestPersistence:
    def test_decision_is_saved_with_its_key(self, tmp_path) -> None:
        _selector(tmp_path, _Probe(PASS)).decide()
        saved = _saved(tmp_path)
        assert saved["decision"] == INT8 and saved["quantizer_version"] == "1"
        assert {"torch_version", "arch", "cpu_flags_hash", "memtotal_class", "reason"} <= set(saved)
        assert saved["memtotal_class"] == "small"

    def test_a_later_failing_probe_does_not_flip_a_saved_int8(self, tmp_path) -> None:
        _selector(tmp_path, _Probe(PASS)).decide()
        probe = _Probe(FAIL)
        assert _selector(tmp_path, probe).decide().precision == INT8 and probe.calls == 0

    def test_a_later_timeout_does_not_flip_a_saved_int8(self, tmp_path) -> None:
        _selector(tmp_path, _Probe(PASS)).decide()
        assert _selector(tmp_path, _Probe(SLOW)).decide().precision == INT8

    def test_a_saved_fp32_is_kept_too(self, tmp_path) -> None:
        _selector(tmp_path, _Probe(FAIL)).decide()
        probe = _Probe(PASS)
        assert _selector(tmp_path, probe).decide().precision == FP32 and probe.calls == 0

    def test_a_new_quantizer_version_is_judged_again(self, tmp_path) -> None:
        _selector(tmp_path, _Probe(FAIL), version="1").decide()
        probe = _Probe(PASS)
        assert _selector(tmp_path, probe, version="2").decide().precision == INT8 and probe.calls == 1
        assert _saved(tmp_path)["quantizer_version"] == "2"

    def test_a_different_cpu_is_judged_again(self, tmp_path) -> None:
        _selector(tmp_path, _Probe(FAIL)).decide()
        probe = _Probe(PASS)
        with patch.object(ep, "_cpu_flags_hash", return_value="other-cpu"):
            assert _selector(tmp_path, probe).decide().precision == INT8
        assert probe.calls == 1

    def test_a_different_torch_is_judged_again(self, tmp_path) -> None:
        _selector(tmp_path, _Probe(FAIL)).decide()
        probe = _Probe(PASS)
        with patch("importlib.metadata.version", return_value="9.9.9"):
            assert _selector(tmp_path, probe).decide().precision == INT8
        assert probe.calls == 1

    def test_a_different_memory_class_is_judged_again(self, tmp_path) -> None:
        _selector(tmp_path, _Probe(FAIL), memtotal=SMALL).decide()
        probe = _Probe(PASS)
        assert _selector(tmp_path, probe, memtotal=LARGE).decide().precision == INT8
        assert probe.calls == 1

    def test_a_corrupt_file_is_ignored(self, tmp_path) -> None:
        (tmp_path / "embedding_precision.json").write_text("{not json")
        probe = _Probe(PASS)
        assert _selector(tmp_path, probe).decide().precision == INT8 and probe.calls == 1

    def test_an_unknown_saved_decision_is_not_trusted(self, tmp_path) -> None:
        _selector(tmp_path, _Probe(PASS)).decide()
        data = _saved(tmp_path)
        data["decision"] = "int4"
        (tmp_path / "embedding_precision.json").write_text(json.dumps(data))
        probe = _Probe(FAIL)
        assert _selector(tmp_path, probe).decide().precision == FP32 and probe.calls == 1

    def test_an_unwritable_location_does_not_stop_the_server(self, tmp_path) -> None:
        blocker = tmp_path / "file"
        blocker.write_text("x")
        selector = PrecisionSelector(
            state_path=blocker / "embedding_precision.json", quantizer_version="1",
            request="int8", memtotal=SMALL, probe=_Probe(PASS),
        )
        assert selector.decide().precision == INT8

    def test_failed_load_downgrades_the_boot_and_keeps_the_saved_decision(self, tmp_path) -> None:
        selector = _selector(tmp_path, _Probe(PASS))
        selector.decide()
        before = (tmp_path / "embedding_precision.json").read_text()
        downgraded = selector.downgrade_for_boot("out of memory")
        assert downgraded.precision == FP32 and "out of memory" in downgraded.reason
        assert selector.decide() is downgraded
        assert (tmp_path / "embedding_precision.json").read_text() == before
        assert _selector(tmp_path, _Probe(FAIL)).decide().precision == INT8  # next boot tries int8 again


def _package_root_for_stub() -> str:
    return ep._package_root()


class TestProbeProcess:
    def test_the_real_module_passes_in_a_child_process(self) -> None:
        result = run_probe(timeout=55)
        assert result.status == "pass", result.detail
        assert "relative error" in result.detail

    def test_child_command_is_isolated_and_gets_the_package_root(self) -> None:
        with patch("subprocess.run") as run:
            run.return_value.returncode, run.return_value.stdout = 0, "ok"
            run_probe()
        cmd = run.call_args.args[0]
        assert cmd[:4] == [sys.executable, "-E", "-s", "-c"]
        assert (Path(cmd[5]) / "app" / "utils" / "int8_linear.py").is_file()
        assert float(cmd[6]) == ep.PROBE_MAX_RELATIVE_ERROR
        assert run.call_args.kwargs["timeout"] == ep.PROBE_TIMEOUT_SECONDS

    @pytest.mark.parametrize(
        ("stub", "status", "needle"),
        [
            ("import sys; sys.exit(1)", "fail", "exited 1"),
            ("import sys; print('boom'); sys.exit(3)", "fail", "boom"),
            # SIGKILL stands in for SIGILL: same "killed by a signal" path, no core dump.
            ("import os, signal; os.kill(os.getpid(), signal.SIGKILL)", "fail", "signal 9"),
            ("import time; time.sleep(30)", "inconclusive", "timed out"),
        ],
    )
    def test_outcomes(self, stub, status, needle) -> None:
        with patch.object(ep, "_PROBE_STUB", stub):
            result = run_probe(timeout=2)
        assert result.status == status and needle in result.detail

    def test_the_stub_rejects_a_layer_that_is_five_percent_off(self) -> None:
        from app.utils.int8_linear import Int8WeightLinear

        real = Int8WeightLinear.forward
        with patch.object(Int8WeightLinear, "forward", lambda self, x: real(self, x) * 1.05), \
                patch.object(sys, "argv", ["probe", _package_root_for_stub(), str(ep.PROBE_MAX_RELATIVE_ERROR)]):
            with pytest.raises(SystemExit) as ended:
                exec(ep._PROBE_STUB, {"__name__": "__main__"})
        assert ended.value.code == 1

    def test_the_stub_accepts_the_real_layer(self) -> None:
        with patch.object(sys, "argv", ["probe", _package_root_for_stub(), str(ep.PROBE_MAX_RELATIVE_ERROR)]):
            with pytest.raises(SystemExit) as ended:
                exec(ep._PROBE_STUB, {"__name__": "__main__"})
        assert ended.value.code == 0

    def test_a_wrong_answer_fails_the_probe(self) -> None:
        stub = "import sys; print('{}'); sys.exit(1)"
        with patch.object(ep, "_PROBE_STUB", stub):
            assert run_probe(timeout=5).status == "fail"

    def test_a_missing_interpreter_is_inconclusive_not_a_crash(self) -> None:
        with patch("subprocess.run", side_effect=OSError("no exec")):
            assert run_probe().status == "inconclusive"
