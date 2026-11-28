"""Download the Docling models the image carries into one directory (run at image build).

    python bake-docling-models.py <dir> [--with-tableformer-fast]

Revisions and file lists come from app/config/constants/docling_models.py, loaded by
path so the build and the runtime lookups share one source. Each model is fetched once,
straight into <dir>/<org>--<name> (Docling's ``artifacts_path`` layout), so it is not
also kept in a Hugging Face cache. Only the files the parsers read are fetched.
"""

import importlib.util
import shutil
import sys
from pathlib import Path
from types import ModuleType


def _constants(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("docling_models", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main(argv: list[str]) -> int:
    from huggingface_hub import snapshot_download

    args = [a for a in argv if not a.startswith("--")]
    with_fast = "--with-tableformer-fast" in argv
    here = Path(__file__).resolve().parent
    candidates = [here / "docling_models.py", here.parent / "backend/python/app/config/constants/docling_models.py"]
    c = _constants(next(p for p in candidates if p.exists()))
    out = Path(args[0])

    modes = list(c.TABLEFORMER_DEFAULT_BAKED) + (["fast"] if with_fast else [])
    jobs = [
        (c.LAYOUT_HERON_REPO, c.LAYOUT_HERON_REVISION, list(c.LAYOUT_HERON_FILES)),
        (c.LAYOUT_HERON_ONNX_REPO, c.LAYOUT_HERON_ONNX_REVISION, list(c.LAYOUT_HERON_ONNX_FILES)),
        (c.TABLEFORMER_REPO, c.TABLEFORMER_REVISION, [f for m in dict.fromkeys(modes) for f in c.tableformer_files(m)]),
    ]
    for repo, revision, files in jobs:
        target = out / repo.replace("/", "--")
        snapshot_download(repo, revision=revision, local_dir=str(target), allow_patterns=files)
        # local_dir keeps download bookkeeping next to the files; the image does not need it.
        shutil.rmtree(target / ".cache", ignore_errors=True)
        missing = [f for f in files if not (target / f).is_file()]
        if missing:
            sys.stderr.write(f"{repo}@{revision}: missing {missing}\n")
            return 1
        sys.stdout.write(f"{repo}@{revision}: {len(files)} files\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
