"""An int8 copy of a model's linear weights, written once and memory-mapped.

Converting at load time reads every fp32 weight page into the server and builds
an anonymous int8 copy, which costs more memory than it saves. Instead a child
process converts the fp32 safetensors file once into a second file holding, per
linear layer, the int8 weights and one fp32 scale per row. The server maps that
file read-only, so the int8 weights are page cache that can be evicted and the
fp32 weights of the linear layers are never read.

The file name carries the model, its revision, the hash of the fp32 file and
the format version, so a model update or a format change builds a new file.
A file is used only after its header, its identity and a checksum of every
tensor match; anything else is rebuilt, never served.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import torch
from torch import nn

from app.utils.int8_linear import Int8WeightLinear, quantize_rows

if TYPE_CHECKING:
    from collections.abc import Callable

FORMAT_VERSION = "1"
BUILD_TIMEOUT_SECONDS = 600.0
_SCALE_SUFFIX = ".weight_scale"
_DTYPES = {"I8": (torch.int8, 1), "F32": (torch.float32, 4)}
_HEX64 = re.compile(r"[0-9a-f]{64}")
_SOURCE_NAME = "model.safetensors"
_CHUNK = 1 << 20


class Int8FileError(Exception):
    """The int8 file cannot be built, found or trusted; the caller serves fp32."""


# ---------------------------------------------------------------------------
# Which layers and which source tensors
# ---------------------------------------------------------------------------


def eligible_linears(root: nn.Module) -> dict[str, nn.Linear]:
    """Linear layers by module path, leaving out any whose weight is tied to an embedding table."""
    tied = {m.weight.data_ptr() for m in root.modules() if isinstance(m, nn.Embedding)}
    return {
        path: m for path, m in root.named_modules()
        if isinstance(m, nn.Linear) and m.weight.data_ptr() not in tied
    }


def source_keys(paths: list[str], file_keys: set[str]) -> dict[str, str]:
    """For each module path the one tensor of the source file that holds its weight."""
    mapping: dict[str, str] = {}
    for path in paths:
        exact = f"{path}.weight"
        if exact in file_keys:
            mapping[path] = exact
            continue
        suffixed = [k for k in file_keys if k.endswith(f".{exact}")]
        if len(suffixed) != 1:
            raise Int8FileError(f"cannot match layer '{path}' to one tensor of the model file")
        mapping[path] = suffixed[0]
    return mapping


# ---------------------------------------------------------------------------
# Reading a safetensors file without loading it
# ---------------------------------------------------------------------------


def read_header(path: Path) -> tuple[dict, int]:
    """The JSON header and the offset where tensor data starts."""
    try:
        with open(path, "rb") as f:
            (length,) = struct.unpack("<Q", f.read(8))
            if not 0 < length <= 64 * 1024 * 1024:
                raise Int8FileError("header length out of range")
            header = json.loads(f.read(length))
        size = path.stat().st_size
    except (OSError, struct.error, ValueError) as exc:
        raise Int8FileError(f"unreadable header: {exc}") from exc
    if not isinstance(header, dict):
        raise Int8FileError("header is not an object")
    start = 8 + length
    end = max((e["data_offsets"][1] for k, e in header.items() if k != "__metadata__"), default=0)
    if start + end != size:
        raise Int8FileError("file size does not match its header")
    return header, start


def _tensor_hash(path: Path, header: dict, start: int) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for name in sorted(k for k in header if k != "__metadata__"):
            begin, end = header[name]["data_offsets"]
            digest.update(name.encode())
            f.seek(start + begin)
            left = end - begin
            while left:
                chunk = f.read(min(_CHUNK, left))
                if not chunk:
                    raise Int8FileError("file ends inside a tensor")
                digest.update(chunk)
                left -= len(chunk)
    return digest.hexdigest()


def verify(path: Path, identity: dict[str, str]) -> dict:
    """Return the header when the file is complete and was built for *identity*."""
    header, start = read_header(path)
    meta = header.get("__metadata__") or {}
    if meta.get("identity") != json.dumps(identity, sort_keys=True):
        raise Int8FileError("file was built for another model, revision or format")
    for name, entry in header.items():
        if name != "__metadata__" and entry.get("dtype") not in _DTYPES:
            raise Int8FileError(f"unexpected dtype for {name}")
    if _tensor_hash(path, header, start) != meta.get("tensors_sha256"):
        raise Int8FileError("checksum mismatch")
    return header


def map_tensors(path: Path) -> dict[str, torch.Tensor]:
    """Every tensor of the file as a view into one read-only private mapping (no copy)."""
    header, start = read_header(path)
    storage = torch.UntypedStorage.from_file(str(path), shared=False, nbytes=path.stat().st_size)
    tensors: dict[str, torch.Tensor] = {}
    for name, entry in header.items():
        if name == "__metadata__":
            continue
        dtype, width = _DTYPES[entry["dtype"]]
        begin, end = entry["data_offsets"]
        offset = start + begin
        if offset % width:
            raise Int8FileError(f"tensor {name} is not aligned")
        count = (end - begin) // width
        view = torch.empty(0, dtype=dtype).set_(storage, offset // width, (count,))
        tensors[name] = view.reshape(entry["shape"])
    return tensors


# ---------------------------------------------------------------------------
# Building (runs in a child process)
# ---------------------------------------------------------------------------


def build(source: Path, destination: Path, key_map: dict[str, str], identity: dict[str, str]) -> None:
    """Write *destination* atomically: temp file in the same directory, fsync, rename, fsync the directory."""
    from safetensors import safe_open
    from safetensors.torch import save_file

    tensors: dict[str, torch.Tensor] = {}
    with safe_open(str(source), framework="pt") as f:
        for path, key in key_map.items():
            wi, scale = quantize_rows(f.get_tensor(key))
            tensors[f"{path}.weight"] = wi.contiguous()
            tensors[f"{path}{_SCALE_SUFFIX}"] = scale.contiguous()

    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        save_file(tensors, str(tmp), metadata={"identity": json.dumps(identity, sort_keys=True), "tensors_sha256": ""})
        header, start = read_header(tmp)
        meta_hash = _tensor_hash(tmp, header, start)
        # The hash is part of the header, so the file is written again with it filled in.
        save_file(tensors, str(tmp), metadata={"identity": json.dumps(identity, sort_keys=True), "tensors_sha256": meta_hash})
        with open(tmp, "rb") as f:
            os.fsync(f.fileno())
        os.replace(tmp, destination)
        dir_fd = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


_BUILD_STUB = """
import json, sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from app.utils.int8_file import build
job = json.loads(Path(sys.argv[2]).read_text())
build(Path(job["source"]), Path(job["destination"]), job["key_map"], job["identity"])
"""


def _package_root() -> str:
    return str(Path(__file__).resolve().parents[2])


def build_in_child(
    source: Path, destination: Path, key_map: dict[str, str], identity: dict[str, str],
    timeout: float = BUILD_TIMEOUT_SECONDS,
) -> None:
    """Build in another process so the conversion's peak memory never sits in the server."""
    with tempfile.TemporaryDirectory() as scratch:
        job = Path(scratch) / "job.json"
        job.write_text(json.dumps({
            "source": str(source), "destination": str(destination), "key_map": key_map, "identity": identity,
        }))
        cmd = [sys.executable, "-E", "-s", "-c", _BUILD_STUB, _package_root(), str(job)]
        try:
            done = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise Int8FileError(f"conversion timed out after {timeout:.0f}s") from exc
        except OSError as exc:
            raise Int8FileError(f"conversion could not start: {exc}") from exc
    if done.returncode != 0:
        raise Int8FileError(f"conversion failed ({done.returncode}): {done.stderr.strip()[-300:]}")


# ---------------------------------------------------------------------------
# Finding or making the file
# ---------------------------------------------------------------------------


def file_identity(source: Path, model_name: str, revision: str) -> dict[str, str]:
    """The model, its revision and the hash of its fp32 file. HF blob names are that hash for LFS files."""
    real = Path(os.path.realpath(source))
    if _HEX64.fullmatch(real.name):
        digest = real.name
    else:
        h = hashlib.sha256()
        with open(real, "rb") as f:
            while chunk := f.read(_CHUNK):
                h.update(chunk)
        digest = h.hexdigest()
    return {"model": model_name, "revision": revision, "source_sha256": digest, "format": FORMAT_VERSION}


def int8_file_path(cache_dir: Path, identity: dict[str, str]) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", identity["model"])
    return cache_dir / f"{safe}-{identity['revision'][:12]}-{identity['source_sha256'][:16]}-v{identity['format']}.safetensors"


def ensure_int8_file(
    model_name: str, snapshot_dir: Path, key_map_for: Callable[[set[str]], dict[str, str]], cache_dir: Path,
    *, build_fn: Callable[..., None] | None = None,
) -> Path:
    """The verified int8 file for this model, built first when missing, stale or damaged.

    Raises ``Int8FileError`` when it cannot be had; the caller then serves fp32.
    """
    source = snapshot_dir / _SOURCE_NAME
    if not source.is_file():
        raise Int8FileError(f"{_SOURCE_NAME} not found; only a single safetensors file is converted")
    identity = file_identity(source, model_name, snapshot_dir.name)
    destination = int8_file_path(cache_dir, identity)
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise Int8FileError(f"cache directory is not writable: {exc}") from exc
    if not os.access(cache_dir, os.W_OK):
        raise Int8FileError(f"cache directory is not writable: {cache_dir}")

    if destination.exists():
        try:
            verify(destination, identity)
            return destination
        except Int8FileError:
            destination.unlink(missing_ok=True)  # a damaged file is rebuilt, never served

    from safetensors import safe_open

    with safe_open(str(source), framework="pt") as f:
        key_map = key_map_for(set(f.keys()))
    (build_fn or build_in_child)(source, destination, key_map, identity)
    verify(destination, identity)
    return destination


def apply_int8_file(root: nn.Module, path: Path) -> int:
    """Swap every eligible linear of *root* for an int8 layer reading from the mapped file.

    All layers are checked before any is swapped, so a failure leaves *root* untouched.
    """
    tensors = map_tensors(path)
    layers = eligible_linears(root)
    stored = {k[: -len(_SCALE_SUFFIX)] for k in tensors if k.endswith(_SCALE_SUFFIX)}
    if not layers or set(layers) != stored:
        raise Int8FileError("the int8 file does not match the model's linear layers")
    built: dict[str, Int8WeightLinear] = {}
    for layer_path, linear in layers.items():
        wi, scale = tensors[f"{layer_path}.weight"], tensors[f"{layer_path}{_SCALE_SUFFIX}"]
        if tuple(wi.shape) != (linear.out_features, linear.in_features):
            raise Int8FileError(f"shape mismatch for '{layer_path}'")
        built[layer_path] = Int8WeightLinear.from_quantized(wi, scale, linear.bias.detach() if linear.bias is not None else None)
    for layer_path, layer in built.items():
        parent_path, _, name = layer_path.rpartition(".")
        parent = root.get_submodule(parent_path) if parent_path else root
        old = layers[layer_path]
        setattr(parent, name, layer)
        old.weight = None  # type: ignore[assignment]
        old.bias = None
    return len(built)
