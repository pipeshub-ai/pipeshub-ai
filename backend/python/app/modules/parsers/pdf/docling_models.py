"""Fill the Hugging Face cache with the models Docling's PDF pipeline loads (C15.04, air-gapped installs).

    python -m app.modules.parsers.pdf.docling_models

The image carries no Docling model: Docling downloads its layout and table models the first time a complex PDF is
parsed, which an install with no route out cannot do. Run this once with a route out, against the volume the install
mounts at HF_HOME (Compose: ``pipeshub_hf_cache``), then start the install with ``HF_HUB_OFFLINE=1``. It builds the
same converter the parser uses, so the cache holds exactly what the service will look for, whatever models a Docling
upgrade picks. Prints each model repository now in the cache; exits 1 when the pipeline could not be built.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def fetch() -> list[str]:
    """Builds the PDF pipeline (which downloads what it lacks) and returns the Docling repositories in the cache."""
    from docling.datamodel.base_models import InputFormat

    from app.modules.parsers.pdf.docling_processor import _get_converter

    _get_converter().initialize_pipeline(InputFormat.PDF)
    return cached_docling_models()


def cached_docling_models() -> list[str]:
    from huggingface_hub.constants import HF_HUB_CACHE

    hub = Path(HF_HUB_CACHE)
    return sorted(p.name.removeprefix("models--").replace("--", "/") for p in hub.glob("models--docling-project--*"))


def main() -> int:
    if os.environ.get("HF_HUB_OFFLINE", "").strip().lower() in ("1", "true", "yes", "on"):
        print("HF_HUB_OFFLINE is on: run this where the models can be downloaded", file=sys.stderr)  # noqa: T201 - CLI
        return 1
    try:
        models = fetch()
    except Exception as error:
        print(f"could not build Docling's PDF pipeline: {type(error).__name__}: {error}", file=sys.stderr)  # noqa: T201 - CLI
        return 1
    for model in models:
        print(model)  # noqa: T201 - CLI output
    return 0


if __name__ == "__main__":
    sys.exit(main())
