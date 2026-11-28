"""The Docling and heron models are read from the directory baked into the image, never from the Hub.

Stub files stand in for the weights: these tests prove which path each loader takes and that the
Hub is not reached. Real weights are exercised by test_real_weights_load_offline when
DOCLING_MODELS_TEST_DIR points at a directory made by deployment/bake-docling-models.py.
"""

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from app.config.constants import docling_models as dm
from app.modules.parsers.pdf import docling_processor, table_detector

REPO_ROOT = Path(__file__).resolve().parents[6]
BAKE_SCRIPT = REPO_ROOT / "deployment" / "bake-docling-models.py"


def _stub_tree(root: Path, *, fast: bool = False, layout: bool = True, onnx: bool = True, accurate: bool = True) -> Path:
    def touch(repo: str, files: tuple[str, ...]) -> None:
        for name in files:
            f = root / repo.replace("/", "--") / name
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b"stub")

    if layout:
        touch(dm.LAYOUT_HERON_REPO, dm.LAYOUT_HERON_FILES)
    if onnx:
        touch(dm.LAYOUT_HERON_ONNX_REPO, dm.LAYOUT_HERON_ONNX_FILES)
    if accurate:
        touch(dm.TABLEFORMER_REPO, dm.tableformer_files("accurate"))
    if fast:
        touch(dm.TABLEFORMER_REPO, dm.tableformer_files("fast"))
    return root


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(dm.DOCLING_ARTIFACTS_ENV, raising=False)
    for warn in (dm._warn_once, dm._warn_mode_once):  # a mutation that drops the cache must fail the 'logs once' test, not this fixture
        getattr(warn, "cache_clear", lambda: None)()
    docling_processor._get_converter.cache_clear()
    monkeypatch.setattr(table_detector, "_sessions", {})
    monkeypatch.setenv("PDF_TABLE_MODEL", "heron")  # the unit conftest turns the detector off
    yield
    docling_processor._get_converter.cache_clear()


def test_default_directory_is_the_image_path() -> None:
    assert dm.artifacts_root() == Path("/opt/docling-models")


def test_env_overrides_the_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(dm.DOCLING_ARTIFACTS_ENV, str(tmp_path))
    assert dm.artifacts_root() == tmp_path


def test_docling_path_resolves_when_layout_and_tableformer_are_baked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(dm.DOCLING_ARTIFACTS_ENV, str(_stub_tree(tmp_path)))
    assert dm.docling_artifacts_path("accurate") == tmp_path
    assert dm.tableformer_dir("accurate") == (
        tmp_path / "docling-project--docling-models" / "model_artifacts" / "tableformer" / "accurate"
    )


@pytest.mark.parametrize("missing", ["layout", "accurate"])
def test_docling_path_is_none_when_a_model_is_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, missing: str) -> None:
    """A partial directory must not be handed to Docling: given a path it never downloads, so it would fail."""
    monkeypatch.setenv(dm.DOCLING_ARTIFACTS_ENV, str(_stub_tree(tmp_path, **{missing: False})))
    assert dm.docling_artifacts_path("accurate") is None


def test_fast_mode_needs_the_fast_weights(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(dm.DOCLING_ARTIFACTS_ENV, str(_stub_tree(tmp_path)))
    assert dm.docling_artifacts_path("fast") is None
    _stub_tree(tmp_path, fast=True)
    assert dm.docling_artifacts_path("fast") == tmp_path


def test_missing_directory_resolves_to_none_and_logs_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(dm.DOCLING_ARTIFACTS_ENV, str(tmp_path / "absent"))
    with caplog.at_level("INFO", logger=dm.logger.name):
        assert dm.docling_artifacts_path() is None
        assert dm.docling_artifacts_path() is None
        assert dm.heron_onnx_path() is None
        assert dm.heron_onnx_path() is None
    assert [r.getMessage().split(" not in")[0] for r in caplog.records] == [
        "Docling layout and TableFormer (accurate) models",
        "Heron table detector model",
    ]


def test_converter_is_built_with_the_baked_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from docling.datamodel.base_models import InputFormat

    monkeypatch.setenv(dm.DOCLING_ARTIFACTS_ENV, str(_stub_tree(tmp_path)))
    opts = docling_processor._get_converter().format_to_options[InputFormat.PDF].pipeline_options
    assert opts.artifacts_path == tmp_path
    # Docling's layout stays on its default PyTorch engine (the ONNX engine costs 1.3 to 1.7x the CPU).
    assert opts.layout_options.engine_options.engine_type.value == "transformers"


def test_converter_falls_back_to_docling_download_without_the_models(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from docling.datamodel.base_models import InputFormat

    monkeypatch.setenv(dm.DOCLING_ARTIFACTS_ENV, str(tmp_path / "absent"))
    opts = docling_processor._get_converter().format_to_options[InputFormat.PDF].pipeline_options
    assert opts.artifacts_path is None


def _pdf_options(monkeypatch: pytest.MonkeyPatch, root: Path, mode: str):  # noqa: ANN202
    from docling.datamodel.base_models import InputFormat

    monkeypatch.setenv(dm.DOCLING_ARTIFACTS_ENV, str(root))
    monkeypatch.setenv("PDF_DOCLING_TABLE_MODE", mode)
    return docling_processor._get_converter().format_to_options[InputFormat.PDF].pipeline_options


def test_fast_mode_without_fast_weights_runs_accurate_from_the_baked_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Passing Docling a path makes it fail on a missing file, so the mode must follow the weights, with a warning."""
    with caplog.at_level("INFO", logger=dm.logger.name):
        opts = _pdf_options(monkeypatch, _stub_tree(tmp_path), "fast")
        docling_processor._get_converter.cache_clear()
        _pdf_options(monkeypatch, tmp_path, "fast")
    assert opts.artifacts_path == tmp_path
    assert opts.table_structure_options.mode.value == "accurate"
    assert len([r for r in caplog.records if "using 'accurate'" in r.getMessage()]) == 1
    assert not [r for r in caplog.records if "downloading on first use" in r.getMessage()]  # nothing is downloaded


def test_fast_mode_with_fast_weights_runs_fast(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    opts = _pdf_options(monkeypatch, _stub_tree(tmp_path, fast=True), "fast")
    assert (opts.artifacts_path, opts.table_structure_options.mode.value) == (tmp_path, "fast")


@pytest.mark.parametrize("missing", ["layout", "accurate"])
def test_partial_directory_leaves_docling_to_download_and_not_to_fail(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, missing: str
) -> None:
    """With a partial directory the pipeline gets no path, and nothing in the environment gives Docling one."""
    from docling.datamodel.settings import AppSettings

    opts = _pdf_options(monkeypatch, _stub_tree(tmp_path, **{missing: False}), "accurate")
    assert opts.artifacts_path is None
    assert AppSettings().artifacts_path is None  # Docling's own variable is not set by our setting


def test_effective_table_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(dm.DOCLING_ARTIFACTS_ENV, str(tmp_path / "absent"))
    assert dm.effective_table_mode("fast") == "fast"  # nothing baked: the requested mode, downloaded as before
    _stub_tree(tmp_path)
    monkeypatch.setenv(dm.DOCLING_ARTIFACTS_ENV, str(tmp_path))
    assert (dm.effective_table_mode("accurate"), dm.effective_table_mode("fast")) == ("accurate", "accurate")
    _stub_tree(tmp_path, fast=True)
    assert dm.effective_table_mode("fast") == "fast"


def test_the_setting_is_not_a_variable_docling_reads() -> None:
    assert dm.DOCLING_ARTIFACTS_ENV != "DOCLING_ARTIFACTS_PATH" and not dm.DOCLING_ARTIFACTS_ENV.startswith("DOCLING_")


@pytest.mark.parametrize("pin", [dm.LAYOUT_HERON_REVISION, dm.LAYOUT_HERON_ONNX_REVISION, dm.TABLEFORMER_REVISION])
def test_every_pin_is_a_commit_sha(pin: str) -> None:
    assert re.fullmatch(r"[0-9a-f]{40}", pin)


class _Session:
    def __init__(self, path: str, *_a: object, **_k: object) -> None:
        self.path = path


def _no_hub(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    calls: list[tuple] = []

    def hf_hub_download(*args: object, **kwargs: object) -> str:
        calls.append((args, kwargs))
        raise AssertionError("the Hub must not be reached")

    monkeypatch.setattr("huggingface_hub.hf_hub_download", hf_hub_download)
    return calls


def test_heron_session_opens_the_baked_file_without_network(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pytest.importorskip("onnxruntime")
    monkeypatch.setenv(dm.DOCLING_ARTIFACTS_ENV, str(_stub_tree(tmp_path)))
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    calls = _no_hub(monkeypatch)
    monkeypatch.setattr("onnxruntime.InferenceSession", _Session)
    sess = table_detector._session("heron")
    assert isinstance(sess, _Session)
    assert sess.path == str(tmp_path / "docling-project--docling-layout-heron-onnx" / "model.onnx")
    assert calls == []


def test_heron_session_downloads_the_pinned_revision_when_not_baked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pytest.importorskip("onnxruntime")
    monkeypatch.setenv(dm.DOCLING_ARTIFACTS_ENV, str(tmp_path / "absent"))
    seen: list[tuple] = []

    def hf_hub_download(repo: str, filename: str, revision: str) -> str:
        seen.append((repo, filename, revision))
        return "/cache/model.onnx"

    monkeypatch.setattr("huggingface_hub.hf_hub_download", hf_hub_download)
    monkeypatch.setattr("onnxruntime.InferenceSession", _Session)
    assert table_detector._session("heron").path == "/cache/model.onnx"
    assert seen == [("docling-project/docling-layout-heron-onnx", "model.onnx", dm.LAYOUT_HERON_ONNX_REVISION)]


def test_bake_script_writes_the_tree_the_resolvers_accept(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The build script and the runtime lookups agree on revisions, folders and files (Hub stubbed)."""
    fetched: list[tuple[str, str]] = []

    def snapshot_download(repo: str, *, revision: str, local_dir: str, allow_patterns: list[str]) -> str:
        fetched.append((repo, revision))
        for name in allow_patterns:
            f = Path(local_dir) / name
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b"w")
        (Path(local_dir) / ".cache").mkdir()
        return local_dir

    monkeypatch.setattr("huggingface_hub.snapshot_download", snapshot_download)
    spec = importlib.util.spec_from_file_location("bake_docling_models", BAKE_SCRIPT)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)

    out = tmp_path / "opt"
    assert script.main([str(out), "--with-tableformer-fast"]) == 0
    assert fetched == [
        (dm.LAYOUT_HERON_REPO, dm.LAYOUT_HERON_REVISION),
        (dm.LAYOUT_HERON_ONNX_REPO, dm.LAYOUT_HERON_ONNX_REVISION),
        (dm.TABLEFORMER_REPO, dm.TABLEFORMER_REVISION),
    ]
    monkeypatch.setenv(dm.DOCLING_ARTIFACTS_ENV, str(out))
    assert dm.docling_artifacts_path("accurate") == out and dm.docling_artifacts_path("fast") == out
    assert dm.heron_onnx_path() is not None
    assert not list(out.rglob(".cache"))


def _accurate_only_view(src: Path, dst: Path) -> Path:
    """The baked directory without the TableFormer fast weights, built from symlinks."""
    src = src.resolve()  # symlink targets resolve from the link's directory, so a relative path would dangle
    dst.mkdir()
    for entry in src.iterdir():
        if entry.name != dm.TABLEFORMER_REPO.replace("/", "--"):
            (dst / entry.name).symlink_to(entry)
    tf = dst / dm.TABLEFORMER_REPO.replace("/", "--") / dm.TABLEFORMER_PATH
    tf.mkdir(parents=True)
    (tf / "accurate").symlink_to(src / dm.TABLEFORMER_REPO.replace("/", "--") / dm.TABLEFORMER_PATH / "accurate")
    return dst


def test_accurate_only_view_accepts_a_relative_models_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tf = dm.TABLEFORMER_REPO.replace("/", "--")
    (tmp_path / "models" / "other").mkdir(parents=True)
    for mode in ("accurate", "fast"):
        (tmp_path / "models" / tf / dm.TABLEFORMER_PATH / mode).mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    view = _accurate_only_view(Path("models"), tmp_path / "view")
    assert (view / "other").resolve() == (tmp_path / "models" / "other").resolve()
    assert (view / tf / dm.TABLEFORMER_PATH / "accurate").resolve().is_dir()
    assert not (view / tf / dm.TABLEFORMER_PATH / "fast").exists()


@pytest.mark.skipif(
    not os.environ.get("DOCLING_MODELS_TEST_DIR"),
    reason="needs the real weights (DOCLING_MODELS_TEST_DIR); run after deployment/bake-docling-models.py --with-tableformer-fast",
)
@pytest.mark.parametrize("mode", ["accurate", "fast", "fast-without-fast-weights"])
def test_real_weights_load_offline(tmp_path: Path, mode: str) -> None:
    """The app's converter, run on a PDF, and the heron ONNX session both work with the Hub forbidden, an empty
    HF cache and an unreachable endpoint, in every table mode, including `fast` when only `accurate` is baked."""
    models = Path(os.environ["DOCLING_MODELS_TEST_DIR"])
    if mode == "fast-without-fast-weights":
        models = _accurate_only_view(models, tmp_path / "view")
    code = """
import sys
from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph
from reportlab.lib.styles import getSampleStyleSheet
pdf = Path(sys.argv[1])
SimpleDocTemplate(str(pdf), pagesize=A4).build([Paragraph("hello", getSampleStyleSheet()["Title"])])
from app.modules.parsers.pdf import docling_processor, table_detector
r = docling_processor._get_converter().convert(pdf)
assert r.status.name == "SUCCESS", r.status
assert table_detector._session("heron") is not None
from docling.datamodel.base_models import InputFormat
conv = docling_processor._get_converter()
print("MODE", conv.format_to_options[InputFormat.PDF].pipeline_options.table_structure_options.mode.value)
"""
    env = {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        "HF_HOME": str(tmp_path / "hf"),
        "HF_HUB_OFFLINE": "1",
        "HF_ENDPOINT": "http://127.0.0.1:9",
        dm.DOCLING_ARTIFACTS_ENV: str(models),
        "PDF_DOCLING_TABLE_MODE": "accurate" if mode == "accurate" else "fast",
        "PDF_TABLE_MODEL": "heron",
        "PYTHONPATH": os.pathsep.join(sys.path),
    }
    env.pop("DOCLING_ARTIFACTS_PATH", None)
    done = subprocess.run([sys.executable, "-c", code, str(tmp_path / "p.pdf")], env=env, capture_output=True, text=True, timeout=600)
    assert done.returncode == 0, done.stderr[-2000:]
    assert f"MODE {'fast' if mode == 'fast' else 'accurate'}" in done.stdout, done.stdout[-500:]  # the mode that actually ran
    assert not (tmp_path / "hf").exists() or not any((tmp_path / "hf").rglob("*.safetensors"))
