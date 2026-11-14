"""The int8 weight file: format, atomic write, rebuild on a stale or damaged file, fallback."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
import torch
from safetensors.torch import save_file
from torch import nn

from app.utils import int8_file
from app.utils.int8_file import (
    Int8FileError,
    apply_int8_file,
    build,
    build_in_child,
    eligible_linears,
    ensure_int8_file,
    file_identity,
    int8_file_path,
    map_tensors,
    read_header,
    source_keys,
    verify,
)
from app.utils.int8_linear import Int8WeightLinear


class _Block(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.up = nn.Linear(16, 32)
        self.down = nn.Linear(32, 16, bias=False)


class _Encoder(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.embed = nn.Embedding(10, 16)
        self.norm = nn.LayerNorm(16)
        self.blocks = nn.ModuleList([_Block(), _Block()])
        self.head = nn.Linear(16, 10)
        self.head.weight = self.embed.weight  # tied to the table


def _snapshot(tmp_path: Path, model: nn.Module, revision: str = "rev1", prefix: str = "") -> Path:
    snap = tmp_path / "hub" / "snapshots" / revision
    snap.mkdir(parents=True)
    save_file({prefix + k: v.contiguous() for k, v in model.state_dict().items() if k != "head.weight"}
              | {prefix + "embed.weight": model.embed.weight.detach().contiguous()}, str(snap / "model.safetensors"))
    return snap


def _ensure(snap: Path, model: nn.Module, cache: Path, build_fn=build):  # noqa: ANN202
    return ensure_int8_file(
        "org/model", snap, lambda keys: source_keys(list(eligible_linears(model)), keys), cache, build_fn=build_fn,
    )


@pytest.fixture
def model() -> nn.Module:
    torch.manual_seed(0)
    return _Encoder().eval()


class TestLayers:
    def test_tied_linears_are_left_out(self, model) -> None:
        assert sorted(eligible_linears(model)) == ["blocks.0.down", "blocks.0.up", "blocks.1.down", "blocks.1.up"]

    def test_a_prefixed_source_key_is_matched_by_suffix(self) -> None:
        assert source_keys(["a.b"], {"model.a.b.weight", "x"}) == {"a.b": "model.a.b.weight"}

    def test_an_ambiguous_or_missing_key_is_refused(self) -> None:
        with pytest.raises(Int8FileError):
            source_keys(["a.b"], {"x.a.b.weight", "y.a.b.weight"})
        with pytest.raises(Int8FileError):
            source_keys(["a.b"], {"other.weight"})


class TestRoundTrip:
    def test_the_file_holds_int8_weights_and_row_scales(self, model, tmp_path) -> None:
        path = _ensure(_snapshot(tmp_path, model), model, tmp_path / "cache")
        tensors = map_tensors(path)
        assert tensors["blocks.0.up.weight"].dtype == torch.int8
        assert tensors["blocks.0.up.weight_scale"].shape == (32,)
        assert len(tensors) == 8

    def test_a_model_backed_by_the_file_gives_nearly_the_same_output(self, model, tmp_path) -> None:
        x = torch.randn(5, 16)
        with torch.no_grad():
            before = model.blocks[0].down(model.blocks[0].up(x))
            path = _ensure(_snapshot(tmp_path, model), model, tmp_path / "cache")
            assert apply_int8_file(model, path) == 4
            after = model.blocks[0].down(model.blocks[0].up(x))
        assert isinstance(model.blocks[0].up, Int8WeightLinear)
        assert isinstance(model.head, nn.Linear) and isinstance(model.embed, nn.Embedding)
        assert float((after - before).norm() / before.norm()) <= 2e-2

    def test_the_weights_are_views_of_the_mapped_file_not_copies(self, model, tmp_path) -> None:
        path = _ensure(_snapshot(tmp_path, model), model, tmp_path / "cache")
        apply_int8_file(model, path)
        storage = model.blocks[0].up.wi.untyped_storage()
        assert storage.nbytes() == path.stat().st_size

    def test_a_source_with_a_key_prefix_converts_and_applies(self, model, tmp_path) -> None:
        snap = _snapshot(tmp_path, model, prefix="bert.")
        path = _ensure(snap, model, tmp_path / "cache")
        assert apply_int8_file(model, path) == 4

    def test_a_file_that_does_not_match_the_model_changes_nothing(self, model, tmp_path) -> None:
        path = _ensure(_snapshot(tmp_path, model), model, tmp_path / "cache")
        other = _Encoder()
        other.blocks.append(_Block())
        with pytest.raises(Int8FileError):
            apply_int8_file(other, path)
        assert all(type(m) is nn.Linear for _, m in eligible_linears(other).items())

    def test_a_file_with_other_layer_shapes_changes_nothing(self, model, tmp_path) -> None:
        path = _ensure(_snapshot(tmp_path, model), model, tmp_path / "cache")
        wider = _Encoder()
        wider.blocks[0].up = nn.Linear(16, 64)
        wider.blocks[0].down = nn.Linear(64, 16, bias=False)
        with pytest.raises(Int8FileError, match="shape"):
            apply_int8_file(wider, path)
        assert type(wider.blocks[0].up) is nn.Linear

    def test_a_model_with_no_linear_layers_is_refused_even_with_an_empty_file(self, tmp_path) -> None:
        table = nn.Sequential(nn.Embedding(4, 4))
        identity = {"model": "m", "revision": "r", "source_sha256": "0" * 64, "format": "1"}
        source = tmp_path / "src.safetensors"
        save_file({"x": torch.zeros(1)}, str(source))
        build(source, tmp_path / "empty.safetensors", {}, identity)
        with pytest.raises(Int8FileError):
            apply_int8_file(table, tmp_path / "empty.safetensors")

    def test_the_real_child_process_builds_the_same_file(self, model, tmp_path) -> None:
        path = _ensure(_snapshot(tmp_path, model), model, tmp_path / "cache", build_fn=build_in_child)
        assert verify(path, file_identity(tmp_path / "hub/snapshots/rev1/model.safetensors", "org/model", "rev1"))

    def test_a_child_that_fails_is_reported_not_raised_as_a_crash(self, model, tmp_path) -> None:
        snap = _snapshot(tmp_path, model)
        with pytest.raises(Int8FileError, match="conversion failed"):
            build_in_child(snap / "model.safetensors", tmp_path / "out.safetensors", {"nope": "missing.weight"}, {"x": "y"})


class TestAtomicWrite:
    def test_a_failure_before_the_rename_leaves_no_file_and_no_temp(self, model, tmp_path) -> None:
        snap, cache = _snapshot(tmp_path, model), tmp_path / "cache"
        with patch.object(int8_file.os, "replace", side_effect=OSError("disk gone")), pytest.raises(OSError):
            _ensure(snap, model, cache)
        assert list(cache.iterdir()) == []

    def test_a_failed_rebuild_keeps_the_previous_file_intact(self, model, tmp_path) -> None:
        snap, cache = _snapshot(tmp_path, model), tmp_path / "cache"
        path = _ensure(snap, model, cache)
        before = path.read_bytes()
        identity = file_identity(snap / "model.safetensors", "org/model", "rev1")
        with patch.object(int8_file.os, "replace", side_effect=OSError("disk gone")), pytest.raises(OSError):
            build(snap / "model.safetensors", path, {"blocks.0.up": "blocks.0.up.weight"}, identity)
        assert path.read_bytes() == before
        assert [p.name for p in cache.iterdir()] == [path.name]

    def test_the_file_is_synced_before_it_is_renamed(self, model, tmp_path) -> None:
        order: list[str] = []
        real_fsync, real_replace = os.fsync, os.replace
        with (
            patch.object(int8_file.os, "fsync", side_effect=lambda fd: (order.append("fsync"), real_fsync(fd))[1]),
            patch.object(int8_file.os, "replace", side_effect=lambda a, b: (order.append("replace"), real_replace(a, b))[1]),
        ):
            _ensure(_snapshot(tmp_path, model), model, tmp_path / "cache")
        assert order[:2] == ["fsync", "replace"] and order[-1] == "fsync"


class TestReuseAndRebuild:
    def test_a_verified_file_is_reused_without_converting(self, model, tmp_path) -> None:
        snap, cache = _snapshot(tmp_path, model), tmp_path / "cache"
        _ensure(snap, model, cache)
        calls: list[int] = []
        _ensure(snap, model, cache, build_fn=lambda *a: calls.append(1))
        assert calls == []

    def test_a_new_revision_or_new_weights_get_a_new_file(self, model, tmp_path) -> None:
        cache = tmp_path / "cache"
        first = _ensure(_snapshot(tmp_path, model, "rev1"), model, cache)
        with torch.no_grad():
            model.blocks[0].up.weight.add_(1.0)
        second = _ensure(_snapshot(tmp_path, model, "rev2"), model, cache)
        assert first != second and first.exists() and second.exists()

    def test_a_file_built_for_another_format_version_is_rebuilt(self, model, tmp_path) -> None:
        snap, cache = _snapshot(tmp_path, model), tmp_path / "cache"
        old = _ensure(snap, model, cache)
        with patch.object(int8_file, "FORMAT_VERSION", "2"):
            new = _ensure(snap, model, cache)
        assert new != old and "-v2" in new.name

    def test_a_stale_identity_inside_the_file_is_rebuilt(self, model, tmp_path) -> None:
        snap, cache = _snapshot(tmp_path, model), tmp_path / "cache"
        path = _ensure(snap, model, cache)
        stale = {**file_identity(snap / "model.safetensors", "org/model", "rev1"), "revision": "old"}
        build(snap / "model.safetensors", path, {k: f"{k}.weight" for k in eligible_linears(model)}, stale)
        calls: list[int] = []
        _ensure(snap, model, cache, build_fn=lambda *a: (calls.append(1), build(*a))[1])
        assert calls == [1]

    def test_a_flipped_byte_is_rebuilt_not_served(self, model, tmp_path) -> None:
        snap, cache = _snapshot(tmp_path, model), tmp_path / "cache"
        path = _ensure(snap, model, cache)
        raw = bytearray(path.read_bytes())
        raw[-5] ^= 0xFF
        path.write_bytes(bytes(raw))
        with pytest.raises(Int8FileError, match="checksum"):
            verify(path, file_identity(snap / "model.safetensors", "org/model", "rev1"))
        calls: list[int] = []
        rebuilt = _ensure(snap, model, cache, build_fn=lambda *a: (calls.append(1), build(*a))[1])
        assert calls == [1]
        verify(rebuilt, file_identity(snap / "model.safetensors", "org/model", "rev1"))

    def test_bytes_appended_to_the_file_are_rebuilt(self, model, tmp_path) -> None:
        snap, cache = _snapshot(tmp_path, model), tmp_path / "cache"
        path = _ensure(snap, model, cache)
        path.write_bytes(path.read_bytes() + b"\x00" * 16)
        with pytest.raises(Int8FileError, match="size"):
            verify(path, file_identity(snap / "model.safetensors", "org/model", "rev1"))
        calls: list[int] = []
        _ensure(snap, model, cache, build_fn=lambda *a: (calls.append(1), build(*a))[1])
        assert calls == [1]

    @pytest.mark.parametrize("keep", [0, 7, 100, -3])
    def test_a_truncated_file_is_rebuilt(self, model, tmp_path, keep) -> None:
        snap, cache = _snapshot(tmp_path, model), tmp_path / "cache"
        path = _ensure(snap, model, cache)
        path.write_bytes(path.read_bytes()[:keep])
        calls: list[int] = []
        _ensure(snap, model, cache, build_fn=lambda *a: (calls.append(1), build(*a))[1])
        assert calls == [1]

    def test_garbage_in_place_of_the_file_is_rebuilt(self, model, tmp_path) -> None:
        snap, cache = _snapshot(tmp_path, model), tmp_path / "cache"
        identity = file_identity(snap / "model.safetensors", "org/model", "rev1")
        cache.mkdir()
        int8_file_path(cache, identity).write_bytes(b"\xff" * 64)
        path = _ensure(snap, model, cache)
        verify(path, identity)


class TestFallbacks:
    def test_an_unwritable_cache_is_reported(self, model, tmp_path) -> None:
        cache = tmp_path / "cache"
        cache.mkdir()
        cache.chmod(0o500)
        try:
            with pytest.raises(Int8FileError, match="not writable"):
                _ensure(_snapshot(tmp_path, model), model, cache)
        finally:
            cache.chmod(0o700)

    def test_a_cache_path_that_is_a_file_is_reported(self, model, tmp_path) -> None:
        blocker = tmp_path / "cache"
        blocker.write_text("not a directory")
        with pytest.raises(Int8FileError, match="not writable"):
            _ensure(_snapshot(tmp_path, model), model, blocker)

    def test_a_model_without_a_single_safetensors_file_is_refused(self, model, tmp_path) -> None:
        snap = tmp_path / "empty"
        snap.mkdir()
        with pytest.raises(Int8FileError, match="model.safetensors"):
            _ensure(snap, model, tmp_path / "cache")

    def test_a_file_that_is_not_safetensors_is_refused(self, tmp_path) -> None:
        bad = tmp_path / "x.safetensors"
        bad.write_bytes(b"\x01\x02")
        with pytest.raises(Int8FileError):
            read_header(bad)

    def test_a_timed_out_conversion_is_reported(self, model, tmp_path) -> None:
        snap = _snapshot(tmp_path, model)
        with patch.object(int8_file, "_BUILD_STUB", "import time; time.sleep(30)"), pytest.raises(Int8FileError, match="timed out"):
            build_in_child(snap / "model.safetensors", tmp_path / "o.safetensors", {}, {}, timeout=1)
