"""Where the Docling models baked into the Docker image live, and how to find them.

The image carries every model the PDF parsers use under one read-only directory
(``/opt/docling-models``) that no volume or mount covers, so an upgrade never hides
them behind an older cache volume and nothing is fetched from the Hugging Face Hub.
The layout follows Docling's own ``artifacts_path`` convention (one folder per
repository, ``org--name``), so the same files serve the Docling pipeline, the pdfplumber
table detector and the table-structure endpoint.

This module imports only the standard library: the image build loads it by file path
(``deployment/bake-docling-models.py``) so the pins below are the single source for
both the build and the runtime.

Outside the image (a from-source install) the directory is absent; the helpers then
return ``None`` and callers fall back to the Hub download they always used, with one
log line saying so. A directory that holds the accurate weights but not the ones a
table mode asks for (``fast`` is not baked by default) uses ``accurate`` instead, with
one warning; nothing is downloaded when the image has the models.
"""

import logging
import os
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

# Not ``DOCLING_ARTIFACTS_PATH``: Docling reads that one itself and, once it is set, never
# downloads (a missing file is then an error, not a fallback). The parsers pass the
# resolved path to their pipeline options only when every file they need is present.
DOCLING_ARTIFACTS_ENV = "PIPESHUB_DOCLING_MODELS_DIR"
DOCLING_ARTIFACTS_DEFAULT = "/opt/docling-models"


def _folder(repo_id: str) -> str:
    return repo_id.replace("/", "--")


# Docling's layout model (its default PyTorch engine loads the safetensors weights).
LAYOUT_HERON_REPO = "docling-project/docling-layout-heron"
LAYOUT_HERON_REVISION = "8f39ad3c0b4c58e9c2d2c84a38465abf757272d8"
LAYOUT_HERON_FILES = ("config.json", "preprocessor_config.json", "model.safetensors")

# The same network exported to ONNX, for the pdfplumber table detector. Docling's layout stage
# stays on the PyTorch weights above: its ONNX engine used 1.3 to 1.7 times the CPU per page
# (identical boxes), so the image keeps both serializations, each once. The label ids in
# table_detector.py belong to this export.
LAYOUT_HERON_ONNX_REPO = "docling-project/docling-layout-heron-onnx"
LAYOUT_HERON_ONNX_REVISION = "40bde044036bb181c130ddf6c51792187268748f"
LAYOUT_HERON_ONNX_FILES = ("config.json", "preprocessor_config.json", "model.onnx")

# TableFormer weights (tag v2.3.0). Docling's table stage and the table-structure
# endpoint read the same files.
TABLEFORMER_REPO = "docling-project/docling-models"
TABLEFORMER_REVISION = "fc0f2d45e2218ea24bce5045f58a389aed16dc23"
TABLEFORMER_PATH = "model_artifacts/tableformer"
TABLEFORMER_MODES = ("accurate", "fast")
# ``fast`` is not baked by default (PDF_DOCLING_TABLE_MODE=fast then runs ``accurate`` with a warning).
TABLEFORMER_DEFAULT_BAKED = ("accurate",)


def tableformer_files(mode: str) -> tuple[str, ...]:
    return (f"{TABLEFORMER_PATH}/{mode}/tm_config.json", f"{TABLEFORMER_PATH}/{mode}/tableformer_{mode}.safetensors")


def artifacts_root() -> Path:
    """The configured models directory (``PIPESHUB_DOCLING_MODELS_DIR`` or the image default)."""
    return Path(os.environ.get(DOCLING_ARTIFACTS_ENV) or DOCLING_ARTIFACTS_DEFAULT)


def _has(root: Path, repo_id: str, files: tuple[str, ...]) -> bool:
    base = root / _folder(repo_id)
    return all((base / name).is_file() for name in files)


@lru_cache(maxsize=None)
def _warn_mode_once(requested: str) -> None:
    logger.warning(
        "Table mode %r needs TableFormer weights that are not in %s; using 'accurate' (bake them with "
        "BUNDLE_TABLEFORMER_FAST=1 to use them)",
        requested,
        artifacts_root(),
    )


@lru_cache(maxsize=None)
def _warn_once(what: str) -> None:
    logger.info(
        "%s not in the image models directory %s; downloading on first use instead",
        what,
        artifacts_root(),
    )


def tableformer_dir(mode: str = "accurate") -> Path | None:
    """Folder holding the TableFormer weights for ``mode`` (the table-structure endpoint's path)."""
    root = artifacts_root()
    if not _has(root, TABLEFORMER_REPO, tableformer_files(mode)):
        return None
    return root / _folder(TABLEFORMER_REPO) / TABLEFORMER_PATH / mode


def heron_onnx_path() -> Path | None:
    """The baked heron ONNX file, or ``None`` when it is not in this install."""
    root = artifacts_root()
    if not _has(root, LAYOUT_HERON_ONNX_REPO, LAYOUT_HERON_ONNX_FILES):
        _warn_once("Heron table detector model")
        return None
    return root / _folder(LAYOUT_HERON_ONNX_REPO) / "model.onnx"


def _docling_models_ready(table_mode: str) -> bool:
    return _has(artifacts_root(), LAYOUT_HERON_REPO, LAYOUT_HERON_FILES) and tableformer_dir(table_mode) is not None


def docling_artifacts_path(table_mode: str = "accurate") -> Path | None:
    """The ``artifacts_path`` for Docling's PDF pipeline, or ``None`` to let Docling download.

    Docling given a path never touches the Hub, so the path is returned only when it holds
    every model the pipeline loads: the layout model and the TableFormer weights for
    ``table_mode``.
    """
    if _docling_models_ready(table_mode):
        return artifacts_root()
    _warn_once(f"Docling layout and TableFormer ({table_mode}) models")
    return None


def effective_table_mode(requested: str = "accurate") -> str:
    """The TableFormer mode to run: ``requested``, or ``accurate`` when the image holds only those weights."""
    # The silent check: the caller then asks for the path of the mode it got, so a fallback to accurate is not
    # also reported as "downloading on first use".
    if requested != "accurate" and not _docling_models_ready(requested) and _docling_models_ready("accurate"):
        _warn_mode_once(requested)
        return "accurate"
    return requested
