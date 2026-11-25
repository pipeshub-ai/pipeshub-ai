"""What a worker build can run: the advertisement sent with every claim (REPORT 6.1.7; P1b RU-2).

Node hands a worker only runs whose requirements this meets, so a fleet may
mix releases: an older worker never receives a run a newer build pinned,
admitted or wrote to. Everything here is a constant of the build, derived
from the models the worker reads with; nothing is configured.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, get_args

from harness_core.contracts.generated.common import Name, PosInt
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.contracts.generated.worker_capabilities import WorkerCapabilities
from harness_core.kernel.fold import FOLD_VERSION

if TYPE_CHECKING:
    from collections.abc import Iterable

# The spec schema versions the pinned RunSpec model accepts.
SUPPORTED_SPEC_SCHEMA_VERSIONS: tuple[int, ...] = get_args(
    RunSpec.model_fields["schema_version"].annotation
)
# Run shapes the kernel folds beyond its fold version: queued input (T2), queued input
# naming attachments and records, consumed as rendered parts (T2b), queued input
# naming its sender (`by`, SS1), which the strict event model must accept, and child
# runs: `child.*` on the stream, a child completing with its result (P6.1), and an approval journaling the
# arguments a person edited (`approval_edits`, AP-06), which a worker without it would ignore and run the model's.
CORE_FEATURES: tuple[str, ...] = (
    "approval_edits",
    "child_runs",
    "steering",
    "steering_by",
    "steering_references",
)


def worker_capabilities(extension_features: Iterable[str] = ()) -> WorkerCapabilities:
    """This build's advertisement; an embedding app adds the shapes its own models accept."""
    return WorkerCapabilities(
        harness_major=2,
        supported_schema_versions=[PosInt(v) for v in SUPPORTED_SPEC_SCHEMA_VERSIONS],
        fold_version=FOLD_VERSION,
        features=[Name(f) for f in sorted({*CORE_FEATURES, *extension_features})],
    )


__all__ = ["CORE_FEATURES", "SUPPORTED_SPEC_SCHEMA_VERSIONS", "worker_capabilities"]
