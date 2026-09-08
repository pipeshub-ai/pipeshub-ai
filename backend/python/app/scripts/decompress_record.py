"""
Standalone script to decompress a PipesHub record JSON file.

Reads a record file (e.g. record_<uuid>.json) that may contain:
  - {"isCompressed": true, "record": "<base64-zstd-msgpack>"}  -> decompresses to dict
  - {"record": {...}}  -> returns record as-is

Outputs the decompressed record as JSON to stdout and optionally to an output file.

Requires: msgspec, zstandard (install from backend/python: pip install -e . or uv sync).

Usage:
  cd backend/python
  python app/scripts/decompress_record.py <path_to_record.json> [--output <out.json>]
  python app/scripts/decompress_record.py --find-record <uuid> [--output <out.json>]

WSL / Explorer paths:
  Linux path:
    ~/.local/PipesHub/<orgId>/PipesHub/records/<uuid>/.../current/record_<uuid>.json
  Windows Explorer (paste into script or use as-is after normalization):
    \\\\wsl.localhost\\Ubuntu\\home\\<user>\\.local\\PipesHub\\...\\record_<uuid>.json

Example:
  python app/scripts/decompress_record.py \\
    ~/.local/PipesHub/6a325a6900bae2f69c61d73e/PipesHub/records/21f97a80-eaa8-4053-abdf-08ec1e1c4b27/6a32741c4f57e1cc8d30b755/current/record_21f97a80-eaa8-4053-abdf-08ec1e1c4b27.json \\
    --output decompressed.json

  python app/scripts/decompress_record.py --find-record 21f97a80-eaa8-4053-abdf-08ec1e1c4b27
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

DEFAULT_WSL_STORAGE_ROOT = Path.home() / ".local" / "PipesHub"


def normalize_input_path(raw: str | Path) -> Path:
    """
    Resolve paths copied from Windows Explorer, file:// URLs, or plain Linux paths.
    """
    s = str(raw).strip().strip('"').strip("'")

    if s.startswith("file://"):
        return Path(unquote(urlparse(s).path))

    # Explorer UNC paths: \\wsl.localhost\Ubuntu\home\user\... or \\wsl$\Ubuntu\...
    unc = s.replace("\\", "/").lstrip("/")
    for marker in ("wsl.localhost/", "wsl$/"):
        marker_lower = marker.lower()
        unc_lower = unc.lower()
        if marker_lower in unc_lower:
            rest = unc[unc_lower.index(marker_lower) + len(marker) :]
            _distro, linux_path = rest.split("/", 1)
            return Path("/" + linux_path)

    # Windows drive letter path -> wslpath when available
    if len(s) >= 2 and s[1] == ":":
        try:
            result = subprocess.run(
                ["wslpath", s],
                capture_output=True,
                text=True,
                check=True,
            )
            return Path(result.stdout.strip())
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass

    return Path(s)


def find_record_file(record_id: str, storage_root: Path) -> Path:
    """Locate record_<uuid>.json under the local PipesHub storage root."""
    filename = f"record_{record_id}.json"
    matches = list(storage_root.rglob(filename))
    if not matches:
        raise FileNotFoundError(
            f"No {filename} found under {storage_root}. "
            f"Explorer: \\\\wsl.localhost\\Ubuntu{storage_root}"
        )
    if len(matches) > 1:
        print("Multiple matches found; using the newest:", file=sys.stderr)
        for match in sorted(matches, key=lambda p: p.stat().st_mtime, reverse=True):
            print(f"  {match}", file=sys.stderr)
    return max(matches, key=lambda p: p.stat().st_mtime)


def decompress_record_data(data: dict) -> dict:
    """
    Process record data: decompress if isCompressed, else return record.
    Mirrors BlobStorage._process_downloaded_record logic.
    """
    import msgspec
    import zstandard as zstd

    if data.get("isCompressed"):
        compressed_base64 = data.get("record")
        if not compressed_base64:
            raise ValueError("isCompressed is true but no 'record' field found")

        compressed_bytes = base64.b64decode(compressed_base64)
        decompressor = zstd.ZstdDecompressor()
        decompressed_bytes = decompressor.decompress(compressed_bytes)
        record = msgspec.msgpack.decode(decompressed_bytes)
        return record

    if data.get("record") is not None:
        return data["record"]

    raise ValueError("Unknown record format: need 'record' and optionally 'isCompressed'")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Decompress a PipesHub record JSON file and output JSON."
    )
    parser.add_argument(
        "record_path",
        type=str,
        nargs="?",
        default=None,
        help="Path to the record JSON file (Linux, WSL UNC, file://, or Windows path)",
    )
    parser.add_argument(
        "--find-record",
        metavar="UUID",
        help=f"Find record_<uuid>.json under {DEFAULT_WSL_STORAGE_ROOT}",
    )
    parser.add_argument(
        "--storage-root",
        type=Path,
        default=DEFAULT_WSL_STORAGE_ROOT,
        help=f"Root for --find-record (default: {DEFAULT_WSL_STORAGE_ROOT})",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Optional path to write decompressed JSON. If omitted, only stdout.",
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=2,
        help="JSON indent for output (default: 2). Use 0 for compact.",
    )
    args = parser.parse_args()

    if args.find_record:
        storage_root = normalize_input_path(args.storage_root).resolve()
        if not storage_root.is_dir():
            print(f"Error: storage root not found: {storage_root}", file=sys.stderr)
            sys.exit(1)
        try:
            record_path = find_record_file(args.find_record, storage_root)
        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"Using: {record_path}", file=sys.stderr)
    elif args.record_path:
        record_path = normalize_input_path(args.record_path).resolve()
    else:
        parser.error("Provide record_path or --find-record")

    if not record_path.is_file():
        print(f"Error: file not found: {record_path}", file=sys.stderr)
        sys.exit(1)

    try:
        with open(record_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"Error: invalid JSON in {record_path}: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        record = decompress_record_data(data)
    except Exception as e:
        print(f"Error: decompression failed: {e}", file=sys.stderr)
        sys.exit(1)

    json_str = json.dumps(record, indent=args.indent, ensure_ascii=False, default=str)
    print(json_str)

    if args.output:
        out_path = normalize_input_path(args.output).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(json_str)
        print(f"\nWritten to: {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
