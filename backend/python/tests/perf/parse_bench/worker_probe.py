"""A job to run inside a parse worker: what the worker's libraries decided about threads."""

from __future__ import annotations

import os
import time


def _status_threads() -> int:
    with open("/proc/self/status") as fh:
        for line in fh:
            if line.startswith("Threads:"):
                return int(line.split()[1])
    return -1


def probe(hold_seconds: float = 1.0) -> dict[str, int | str]:
    """Imports OpenCV as the layout analyzer does and reports its thread count.

    *hold_seconds* keeps the worker busy so that concurrent probes land on different workers.
    """
    import cv2

    time.sleep(hold_seconds)
    return {
        "pid": os.getpid(),
        "cv2_threads": int(cv2.getNumThreads()),
        "cv2_cpus": int(cv2.getNumberOfCPUs()),
        "affinity": len(os.sched_getaffinity(0)),
        "os_threads": _status_threads(),
        "onnx_threads_env": os.environ.get("PDF_TABLE_MODEL_THREADS", ""),
        "omp_threads_env": os.environ.get("OMP_NUM_THREADS", ""),
    }
