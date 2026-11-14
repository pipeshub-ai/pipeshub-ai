"""Which weight precision the bundled embedding server runs, and why.

``EMBEDDING_SERVER_PRECISION`` is ``auto``, ``int8`` or ``fp32`` (empty means
unset, which is ``auto``). ``auto`` currently always means fp32; the memory
threshold below is what turns it into an int8 default for small hosts.

Choosing int8 first runs a correctness probe in a child process, so a CPU or
torch build that cannot run the kernel ends in fp32 instead of a dead server.
The outcome is stored next to the model cache and reused until the torch
version, the quantiser version, the CPU or the memory class changes, so a boot
never flips precision by timing luck.

This module imports no torch: the server must start without it, and the probe
child is where the int8 layer is first exercised.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from app.utils.logger import create_logger

if TYPE_CHECKING:
    from collections.abc import Callable

logger = create_logger("embedding_precision")

# Bump when the stored representation or the rounding changes: saved decisions are keyed on it.
QUANTIZER_VERSION = "1"

PRECISION_ENV = "EMBEDDING_SERVER_PRECISION"
STATE_FILENAME = "embedding_precision.json"

FP32 = "fp32"
INT8 = "int8-w"

_GIB = 1024**3

# Hosts with less memory than this get int8 under ``auto``. 0 means no host
# does, so ``auto`` is fp32 everywhere. Set it to 12 * _GIB to enable the default.
AUTO_INT8_BELOW_MEMTOTAL_BYTES = 0
# Hosts at or above this are one memory class, the rest another; the class is
# part of the persisted key so a resized machine is judged again.
_MEMORY_CLASS_BOUNDARY_BYTES = 12 * _GIB

PROBE_TIMEOUT_SECONDS = 60.0
PROBE_MAX_RELATIVE_ERROR = 1e-2

_VALID_REQUESTS = ("auto", "int8", "fp32")


def requested_precision(raw: str | None = None) -> str:
    """The requested mode from the environment; an unknown value is an error, not a silent default."""
    value = (os.getenv(PRECISION_ENV) if raw is None else raw) or ""
    value = value.strip().lower()
    if not value:
        return "auto"
    if value not in _VALID_REQUESTS:
        raise ValueError(f"{PRECISION_ENV} must be one of auto, int8, fp32, got {value!r}")
    return value


def host_memory_total_bytes() -> int | None:
    """MemTotal of the machine (not the container limit); None when unreadable."""
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    try:
        return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (ValueError, OSError, AttributeError):
        return None


def _cpu_flags_hash() -> str:
    flags = ""
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith(("flags", "Features")):
                flags = line.split(":", 1)[1].strip()
                break
    except OSError:
        flags = platform.processor()
    return hashlib.sha256(flags.encode()).hexdigest()[:16]


def environment_key(memtotal: int | None, quantizer_version: str) -> dict[str, str]:
    import importlib.metadata as md

    try:
        torch_version = md.version("torch")
    except md.PackageNotFoundError:
        torch_version = "none"
    small = memtotal is not None and memtotal < _MEMORY_CLASS_BOUNDARY_BYTES
    return {
        "torch_version": torch_version,
        "quantizer_version": quantizer_version,
        "arch": platform.machine(),
        "cpu_flags_hash": _cpu_flags_hash(),
        "memtotal_class": "small" if small else "large",
    }


# ---------------------------------------------------------------------------
# Probe
# ---------------------------------------------------------------------------

# The stub gets the package root as argv so it tests the module the server runs.
_PROBE_STUB = """
import json, sys
sys.path.insert(0, sys.argv[1])
import torch
from app.utils.int8_linear import Int8WeightLinear
torch.manual_seed(0)
linear = torch.nn.Linear(256, 256)
layer = Int8WeightLinear.from_linear(linear)
worst = 0.0
for rows in (1, 33):
    x = torch.randn(rows, 256)
    ref = linear(x)
    out = layer(x)
    if not bool(torch.isfinite(out).all()):
        print(json.dumps({"ok": False, "detail": "non-finite output"})); sys.exit(1)
    worst = max(worst, float((out - ref).norm() / ref.norm()))
limit = float(sys.argv[2])
print(json.dumps({"ok": worst <= limit, "detail": "relative error %.2e" % worst}))
sys.exit(0 if worst <= limit else 1)
"""

ProbeStatus = Literal["pass", "fail", "inconclusive"]


@dataclass(frozen=True)
class ProbeResult:
    status: ProbeStatus
    detail: str


def _package_root() -> str:
    return str(Path(__file__).resolve().parents[2])


def run_probe(timeout: float = PROBE_TIMEOUT_SECONDS) -> ProbeResult:
    """Run the correctness probe in a child process. No timing is judged."""
    cmd = [sys.executable, "-E", "-s", "-c", _PROBE_STUB, _package_root(), str(PROBE_MAX_RELATIVE_ERROR)]
    try:
        done = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        return ProbeResult("inconclusive", f"probe timed out after {timeout:.0f}s")
    except OSError as exc:
        return ProbeResult("inconclusive", f"probe could not start: {exc}")
    if done.returncode < 0:
        return ProbeResult("fail", f"probe killed by signal {-done.returncode}")
    if done.returncode != 0:
        tail = (done.stdout.strip() or done.stderr.strip())[-200:]
        return ProbeResult("fail", f"probe exited {done.returncode}: {tail}")
    return ProbeResult("pass", done.stdout.strip()[-200:])


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PrecisionDecision:
    precision: str  # FP32 or INT8
    reason: str
    quantizer_version: str
    # What the setting asks for, before any probe or load can fail: INT8 for ``int8`` (or ``auto`` on a
    # host under the auto threshold), otherwise FP32. Indexes are matched against it, not against ``precision``.
    intent: str = FP32

    @property
    def fallback(self) -> bool:
        """int8 was asked for and fp32 is what serves."""
        return self.intent == INT8 and self.precision == FP32


def _read_state(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_state(path: Path, record: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".embedding_precision.")
        try:
            with os.fdopen(fd, "w") as handle:
                json.dump(record, handle, sort_keys=True)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    except OSError:
        logger.warning("Could not save the embedding precision decision to %s", path, exc_info=True)


class PrecisionSelector:
    """Decides once per process; a failed int8 load downgrades this boot only."""

    def __init__(
        self,
        *,
        state_path: Path | Callable[[], Path],
        quantizer_version: str,
        request: str | None = None,
        memtotal: int | None = None,
        probe: Callable[[], ProbeResult] = run_probe,
    ) -> None:
        self._state_path = state_path
        self._quantizer_version = quantizer_version
        self._request = request
        self._memtotal = memtotal
        self._probe = probe
        self._lock = threading.Lock()
        self._decision: PrecisionDecision | None = None

    @property
    def decision(self) -> PrecisionDecision | None:
        return self._decision

    def decide(self, device: str = "cpu") -> PrecisionDecision:
        with self._lock:
            if self._decision is None:
                self._decision = self._compute(device)
                logger.info(
                    "Embedding precision: %s (%s), quantizer version %s",
                    self._decision.precision, self._decision.reason, self._quantizer_version,
                )
            return self._decision

    def intent(self, device: str = "cpu") -> str:
        """What the setting asks for, with no probe and no load: INT8 or FP32."""
        request = self._request if self._request is not None else requested_precision()
        if request == "fp32" or device != "cpu":
            return FP32
        if request == "int8":
            return INT8
        memtotal = self._memtotal if self._memtotal is not None else host_memory_total_bytes()
        return FP32 if memtotal is None or memtotal >= AUTO_INT8_BELOW_MEMTOTAL_BYTES else INT8

    def downgrade_for_boot(self, why: str) -> PrecisionDecision:
        """An int8 load failed: fp32 until the next boot. The stored decision is left as it is."""
        with self._lock:
            intent = self._decision.intent if self._decision is not None else INT8
            self._decision = PrecisionDecision(FP32, f"int8 load failed, fp32 for this boot: {why}", self._quantizer_version, intent)
            logger.error(
                "Embedding precision: int8 could not be loaded, using fp32 for this boot (%s). "
                "The saved decision is unchanged and int8 is tried again on the next start.", why,
            )
            return self._decision

    def _compute(self, device: str) -> PrecisionDecision:
        version = self._quantizer_version
        request = self._request if self._request is not None else requested_precision()
        memtotal = self._memtotal if self._memtotal is not None else host_memory_total_bytes()
        if request == "fp32":
            return PrecisionDecision(FP32, f"{PRECISION_ENV}=fp32", version)
        if device != "cpu":
            return PrecisionDecision(FP32, f"int8 weights are only used on cpu, device is {device}", version)
        if request == "auto":
            if memtotal is None or memtotal >= AUTO_INT8_BELOW_MEMTOTAL_BYTES:
                return PrecisionDecision(FP32, "auto: fp32 on this host", version)
            reason_prefix = f"auto: MemTotal {memtotal / _GIB:.1f} GiB"
        else:
            reason_prefix = f"{PRECISION_ENV}=int8"

        path = self._state_path() if callable(self._state_path) else self._state_path
        key = environment_key(memtotal, version)
        saved = _read_state(path)
        if saved and all(saved.get(k) == v for k, v in key.items()) and saved.get("decision") in (FP32, INT8):
            return PrecisionDecision(saved["decision"], f"{reason_prefix}; saved decision: {saved.get('reason', '')}", version, INT8)

        result = self._probe()
        if result.status == "inconclusive":
            result = self._probe()
        if result.status == "inconclusive":
            return PrecisionDecision(FP32, f"{reason_prefix}; probe inconclusive, fp32 for this boot: {result.detail}", version, INT8)
        chosen = INT8 if result.status == "pass" else FP32
        _write_state(path, {**key, "decision": chosen, "reason": result.detail})
        return PrecisionDecision(chosen, f"{reason_prefix}; probe {result.status}: {result.detail}", version, INT8)


def model_cache_state_path() -> Path:
    from huggingface_hub.constants import HF_HUB_CACHE

    return Path(HF_HUB_CACHE) / STATE_FILENAME


