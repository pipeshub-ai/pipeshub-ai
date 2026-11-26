#!/usr/bin/env python3
"""The engine upgrade pipeline's candidate catalog (C14.03, ADR-OPS-01 D7; CC1's candidate gate).

A scheduled candidate job runs this, nightly, for every engine kind in
backend/python/harness_engines/allowlist.json:

    scripts/engine_candidate_catalog.py sources [--format github-matrix]
        how each kind's upstream `latest` is found and built: a GitHub release (the allowlist's asset URL names
        the repository and the tag's prefix), npm for an admin-supplied engine (Claude Code, D29: the version is
        reported, never downloaded or built here), or the kind whose binary and image it shares (acp-opencode).
    scripts/engine_candidate_catalog.py decide --kind K --tag TAG
        `skip` when the upstream version is already allow-listed for K, `candidate` when it is not, `unresolved`
        when the tag is not a version of K's release line. Prints JSON {action, kind, version}.
    scripts/engine_candidate_catalog.py asset-url --kind K --version V
        K's release asset URL for V (the allowlisted URL with the version moved).
    scripts/engine_candidate_catalog.py binary-digest --kind K --asset PATH
        sha256 of the binary (the allowlist's `member`) inside a downloaded asset, for --digest and the gate.
    scripts/engine_candidate_catalog.py lanes --kind K
        CC1's gate for K (E9 "Admitting a new engine version" step 2) as JSON [{name, path, select}].
    scripts/engine_candidate_catalog.py entry --kind K --version V --image I --image-digest D --binary-digest B
        [--asset-sha256 S] --lanes lanes.json --run-url URL --out entry.json
        writes the candidate catalog entry, after validating it; refused unless every gate lane passed.
    scripts/engine_candidate_catalog.py validate entry.json
    scripts/engine_candidate_catalog.py draft-patch entry.json --out allowlist.patch
        the allowlist change admission would make, as a unified diff file. Never applied or committed here.

What the pipeline does not do, by design: it never edits allowlist.json, never pushes a branch, never opens a pull
request. A candidate reaches people in two product steps after this:
1. Per-org opt-in: a reviewed release ships the candidate as an extra allowlist entry of its kind, after the
   kind's current default (the draft patch has that shape). An org admin opts in by naming `kind@version` in the
   org's engine profile ceiling (`engineProfile.engines`); the first release engine every layer admits runs.
2. Default flip: a later reviewed release makes it the kind's first entry, which is the version new conversations
   start on. A conversation keeps the version it started on while that version stays listed and its org allows it.
Admission also moves Node's RELEASE_ENGINE_PROFILES and contracts/harness/vectors/engine-profiles.json (and an ACP
profile in acp_agents.json, re-signed at release); the draft patch names them but changes only the allowlist.
Standard library only.
"""

from __future__ import annotations

import argparse
import copy
import difflib
import hashlib
import json
import re
import sys
import tarfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import io

ROOT = Path(__file__).resolve().parent.parent
ALLOWLIST = ROOT / "backend/python/harness_engines/allowlist.json"
ALLOWLIST_REL = "backend/python/harness_engines/allowlist.json"
SCHEMA_VERSION = 1
CANDIDATE_SUFFIX = "-candidate"
# The npm package an admin-supplied engine's version is read from (its binary never is).
ADMIN_PACKAGES = {"claude_code": "@anthropic-ai/claude-code"}
VERSION = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,100}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
HEX64 = re.compile(r"[0-9a-f]{64}")
RUN_URL = re.compile(r"https://github\.com/[^/\s]+/[^/\s]+/actions/runs/\d+(?:/attempts/\d+)?")
_GITHUB_ASSET = re.compile(r"https://github\.com/(?P<repo>[^/]+/[^/]+)/releases/download/(?P<tag>[^/]+)/[^/]+")
# An npm release tarball (Qwen Code, WP-91): its latest is the registry's ``version``, its tag the bare version.
_NPM_ASSET = re.compile(r"https://registry\.npmjs\.org/(?P<package>(?:@[^/]+/)?[^/]+)/-/(?P<file>[^/]+)\.tgz")
LANE_RESULTS = ("pass", "fail", "skipped")
ENTRY_KEYS = {
    "schemaVersion", "kind", "version", "previous", "image", "imageDigest", "binaryDigest", "assetSha256", "gate",
    "runUrl", "createdAt",
}

# CC1's gate on runc (E9 step 2), paths relative to backend/python; `select` is the lane's -k.
_IMAGE = "tests/unit/harness/sandbox_broker/test_engine_image_docker.py"
_MATRIX = "tests/unit/harness/engines/test_e2e3_tool_matrix.py"
_F6 = "tests/unit/harness/engines/test_e2e3_f6_mcp_startup_real_engines.py"
_UNIT = ("candidate-isolation", "tests/unit/harness/engines/test_cc1_candidate.py", "")
GATE_LANES: dict[str, tuple[tuple[str, str, str], ...]] = {
    "opencode": (
        _UNIT,
        ("image", _IMAGE, "opencode"),
        ("conformance", "tests/unit/harness/engines/test_real_conformance.py", ""),
        ("controller", "tests/unit/harness/engines/test_real_controller.py", ""),
        ("tool-matrix", _MATRIX, "opencode"),
    ),
    "codex": (
        _UNIT,
        ("image", _IMAGE, "codex"),
        # Includes R10-CC1-H2: the candidate's hook hash must equal TRUSTED_HOOKS.
        ("conformance", "tests/unit/harness/engines/test_real_codex_conformance.py", ""),
        ("controller", "tests/unit/harness/engines/test_real_codex_controller.py", ""),
        ("tool-matrix", _MATRIX, "codex"),
    ),
    "claude_code": (
        _UNIT,
        ("image", _IMAGE, "claude_code"),
        ("conformance", "tests/unit/harness/engines/test_real_claude_code_conformance.py", ""),
        ("controller", "tests/unit/harness/engines/test_real_claude_code_controller.py", ""),
        ("tool-matrix", _MATRIX, "claude_code"),
    ),
    "acp-opencode": (
        _UNIT,
        ("conformance", "tests/unit/harness/engines/test_real_acp_conformance.py", ""),
        ("mcp-startup", _F6, "acp-opencode"),
    ),
    "acp-gemini": (
        _UNIT,
        ("conformance", "tests/unit/harness/engines/test_real_gemini_acp_conformance.py", ""),
        ("mcp-startup", _F6, "acp-gemini"),
    ),
    "acp-goose": (
        _UNIT,
        ("conformance", "tests/unit/harness/engines/test_real_goose_acp_conformance.py", ""),
    ),
    "acp-qwen": (
        _UNIT,
        ("conformance", "tests/unit/harness/engines/test_real_qwen_acp_conformance.py", ""),
    ),
    "acp-dsh": (
        _UNIT,
        ("conformance", "tests/unit/harness/engines/test_real_dsh_acp_conformance.py", ""),
    ),
}


class CatalogError(ValueError):
    """An entry, a kind or an asset the pipeline refuses."""


def load_allowlist(path: Path = ALLOWLIST) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def kinds(allowlist: dict[str, Any]) -> list[str]:
    return list(dict.fromkeys(e["kind"] for e in allowlist["engines"]))


def entries_of(allowlist: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    found = [e for e in allowlist["engines"] if e["kind"] == kind]
    if not found:
        raise CatalogError(f"{kind} is not an allow-listed engine kind")
    return found


def default_entry(allowlist: dict[str, Any], kind: str) -> dict[str, Any]:
    """The kind's first entry: the version new conversations start on."""
    return entries_of(allowlist, kind)[0]


def repository(image: str) -> str:
    """The repository part of an image reference (a registry port is not a tag), as harness_engines.release does."""
    name, sep, tag = image.rpartition(":")
    return name if sep and "/" not in tag else image


def candidate_image(image: str, version: str) -> str:
    """The tag `engine-build.sh --candidate` gives `version` of the engine whose run image is `image`."""
    return f"{repository(image)}:{version}{CANDIDATE_SUFFIX}"


def source(allowlist: dict[str, Any], kind: str) -> dict[str, Any]:
    """How `kind`'s latest upstream version is found and built."""
    entry = default_entry(allowlist, kind)
    base = {
        "kind": kind,
        "allowlisted": entry["version"],
        "listed": [e["version"] for e in entries_of(allowlist, kind)],
        "image": entry["image"],
        "variants": [n for n in (entry.get("variants") or {}) if not n.startswith("_")],
    }
    if entry.get("supply") == "admin":
        return {**base, "resolve": "npm", "package": ADMIN_PACKAGES[kind], "build": "admin", "buildKind": kind,
                "repo": "", "tagPrefix": ""}
    asset = entry.get("asset")
    if asset is None:
        owner = next(
            (o for o in allowlist["engines"] if o.get("asset") and o["image"] == entry["image"] and o["kind"] != kind),
            None,
        )
        if owner is None:
            raise CatalogError(f"{kind} has no asset and shares no other kind's image")
        followed = source(allowlist, owner["kind"])
        return {**base, "resolve": "github-release", "repo": followed["repo"], "tagPrefix": followed["tagPrefix"],
                "package": "", "build": "follows", "buildKind": owner["kind"],
                "variants": followed["variants"]}
    npm = _NPM_ASSET.fullmatch(asset["url"])
    if npm is not None and npm["file"].endswith("-" + entry["version"]):
        return {**base, "resolve": "npm", "package": npm["package"], "build": "release", "buildKind": kind,
                "repo": "", "tagPrefix": ""}
    match = _GITHUB_ASSET.fullmatch(asset["url"])
    if match is None or not match["tag"].endswith(entry["version"]):
        raise CatalogError(f"{kind}'s asset URL is not a GitHub release of {entry['version']}: {asset['url']}")
    return {**base, "resolve": "github-release", "repo": match["repo"],
            "tagPrefix": match["tag"][: -len(entry["version"])], "package": "", "build": "release", "buildKind": kind}


def sources(allowlist: dict[str, Any]) -> list[dict[str, Any]]:
    return [source(allowlist, k) for k in kinds(allowlist)]


def decide(allowlist: dict[str, Any], kind: str, tag: str) -> dict[str, str]:
    """The skip rule: nothing to do when upstream's latest is a version this kind already lists."""
    src = source(allowlist, kind)
    tag = tag.strip()
    version = tag[len(src["tagPrefix"]):] if tag.startswith(src["tagPrefix"]) else ""
    if not version or not VERSION.fullmatch(version):
        return {"action": "unresolved", "kind": kind, "version": "", "tag": tag}
    action = "skip" if version in src["listed"] else "candidate"
    return {"action": action, "kind": kind, "version": version, "tag": tag}


def asset_url(allowlist: dict[str, Any], kind: str, version: str) -> str:
    entry = default_entry(allowlist, source(allowlist, kind)["buildKind"])
    if "asset" not in entry:
        raise CatalogError(f"{kind} has no release asset (supply: {entry.get('supply', 'release')})")
    if not VERSION.fullmatch(version):
        raise CatalogError(f"{version!r} is not a tag-safe version")
    old = entry["asset"]["url"]
    npm = _NPM_ASSET.fullmatch(old)
    if npm is not None:
        file = npm["file"]
        return old.replace(f"/-/{file}.tgz", f"/-/{file[: -len(entry['version'])]}{version}.tgz", 1)
    match = _GITHUB_ASSET.fullmatch(old)
    assert match is not None
    tag = match["tag"]
    return old.replace(f"/download/{tag}/", f"/download/{tag[: -len(entry['version'])]}{version}/", 1)


def _safe(name: str) -> None:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise CatalogError(f"unsafe path in the asset: {name}")


def binary_digest(allowlist: dict[str, Any], kind: str, asset: Path) -> str:
    """sha256 of the allowlist's `member` inside `asset` (tar.gz, or zip for a `tree` asset); read, never unpacked."""
    src = source(allowlist, kind)
    entry = default_entry(allowlist, src["buildKind"])
    member = entry["asset"]["member"]
    _safe(member)
    wanted = PurePosixPath(member)
    if zipfile.is_zipfile(asset):
        with zipfile.ZipFile(asset) as archive:
            for info in archive.infolist():
                if PurePosixPath(info.filename) == wanted:
                    return "sha256:" + hashlib.sha256(archive.read(info)).hexdigest()
    else:
        with tarfile.open(asset, "r:gz") as archive:
            for info in archive.getmembers():
                if PurePosixPath(info.name) == wanted and info.isfile():
                    data = archive.extractfile(info)
                    assert data is not None
                    return "sha256:" + hashlib.sha256(data.read()).hexdigest()
    raise CatalogError(f"{member} is not in {asset.name}")


def lanes(kind: str) -> list[dict[str, str]]:
    if kind not in GATE_LANES:
        raise CatalogError(f"no gate lanes for {kind}")
    return [{"name": n, "path": p, "select": s} for n, p, s in GATE_LANES[kind]]


def validate(entry: object, allowlist: dict[str, Any]) -> list[str]:
    """Every reason `entry` is not a candidate catalog entry; empty when it is one."""
    if not isinstance(entry, dict):
        return ["an entry is a JSON object"]
    errors: list[str] = []
    missing, extra = ENTRY_KEYS - entry.keys(), entry.keys() - ENTRY_KEYS
    if missing:
        errors.append(f"missing: {', '.join(sorted(missing))}")
    if extra:
        errors.append(f"unknown: {', '.join(sorted(extra))}")
    if errors:
        return errors
    if entry["schemaVersion"] != SCHEMA_VERSION:
        errors.append(f"schemaVersion is {SCHEMA_VERSION}")
    kind = entry["kind"]
    if kind not in kinds(allowlist):
        return [*errors, f"kind {kind!r} is not allow-listed"]
    src = source(allowlist, kind)
    version = entry["version"]
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        errors.append("version is not a tag-safe version")
    elif version in src["listed"]:
        errors.append(f"{kind} {version} is already allow-listed: nothing to admit")
    if entry["previous"] != src["allowlisted"]:
        errors.append(f"previous is {kind}'s allow-listed default, {src['allowlisted']}")
    if isinstance(version, str) and entry["image"] != candidate_image(src["image"], version):
        errors.append(f"image is {candidate_image(src['image'], version)} (the candidate tag)")
    errors.extend(
        f"{key} is sha256:<64 hex>"
        for key in ("imageDigest", "binaryDigest")
        if not isinstance(entry[key], str) or not DIGEST.fullmatch(entry[key])
    )
    needs_asset = src["build"] != "admin"
    sha = entry["assetSha256"]
    if needs_asset and (not isinstance(sha, str) or not HEX64.fullmatch(sha)):
        errors.append("assetSha256 is the release asset's 64 hex")
    if not needs_asset and sha is not None:
        errors.append("assetSha256 is null for an admin-supplied engine")
    errors += _gate_errors(entry["gate"], kind)
    if not isinstance(entry["runUrl"], str) or not RUN_URL.fullmatch(entry["runUrl"]):
        errors.append("runUrl is the GitHub Actions run that gated it")
    try:
        datetime.fromisoformat(str(entry["createdAt"]).replace("Z", "+00:00"))
    except ValueError:
        errors.append("createdAt is an ISO-8601 time")
    return errors


def _gate_errors(gate: object, kind: str) -> list[str]:
    if not isinstance(gate, dict) or set(gate) != {"result", "lanes"} or not isinstance(gate["lanes"], dict):
        return ["gate is {result, lanes}"]
    errors = []
    wanted = {lane["name"] for lane in lanes(kind)}
    if set(gate["lanes"]) != wanted:
        errors.append(f"gate.lanes are {kind}'s gate: {', '.join(sorted(wanted))}")
    if any(v not in LANE_RESULTS for v in gate["lanes"].values()):
        errors.append(f"a lane result is one of {', '.join(LANE_RESULTS)}")
    passed = bool(gate["lanes"]) and all(v == "pass" for v in gate["lanes"].values())
    if gate["result"] != ("pass" if passed else "fail"):
        errors.append("gate.result is pass exactly when every lane passed")
    if gate["result"] != "pass":
        errors.append("a catalog entry is written only for a candidate whose gate passed")
    return errors


def make_entry(allowlist: dict[str, Any], *, kind: str, version: str, image: str, image_digest: str,
               binary_digest: str, asset_sha256: str | None, lane_results: dict[str, str], run_url: str,
               now: datetime | None = None) -> dict[str, Any]:
    passed = bool(lane_results) and all(v == "pass" for v in lane_results.values())
    entry = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": kind,
        "version": version,
        "previous": default_entry(allowlist, kind)["version"],
        "image": image,
        "imageDigest": image_digest,
        "binaryDigest": binary_digest,
        "assetSha256": asset_sha256,
        "gate": {"result": "pass" if passed else "fail", "lanes": dict(sorted(lane_results.items()))},
        "runUrl": run_url,
        "createdAt": (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    errors = validate(entry, allowlist)
    if errors:
        raise CatalogError("; ".join(errors))
    return entry


def _retag(tag: str, old: str, new: str) -> str:
    repo, _, version_tag = tag.rpartition(":")
    return f"{repo}:{version_tag.replace(old, new, 1)}"


def draft_allowlist(allowlist: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    """The allowlist with the candidate as an extra entry after its kind's last one: the default stays (opt-in)."""
    errors = validate(entry, allowlist)
    if errors:
        raise CatalogError("; ".join(errors))
    draft = copy.deepcopy(allowlist)
    kind, old_entry = entry["kind"], default_entry(allowlist, entry["kind"])
    old, new = old_entry["version"], entry["version"]
    added = copy.deepcopy(old_entry)
    added["_comment"] = (
        f"Candidate {kind} {new}, gated by {entry['runUrl']} (the candidate job). Draft: an org opts in with "
        f"`{kind}@{new}` in its engine profile ceiling once a reviewed release ships this entry; the default flips "
        "only when a later reviewed release makes it the kind's first entry."
    )
    added["version"] = new
    added["binaryDigest"] = entry["binaryDigest"]
    added["image"] = _retag(old_entry["image"], old, new)
    if "variants" in added:
        added["variants"] = {
            k: (v if k.startswith("_") else _retag(v, old, new)) for k, v in old_entry["variants"].items()
        }
    if "asset" in added:
        added["asset"]["url"] = asset_url(allowlist, kind, new)
        added["asset"]["sha256"] = entry["assetSha256"]
    last = max(i for i, e in enumerate(draft["engines"]) if e["kind"] == kind)
    draft["engines"].insert(last + 1, added)
    return draft


def render(allowlist: dict[str, Any]) -> str:
    return json.dumps(allowlist, indent=2, ensure_ascii=False) + "\n"


def draft_patch(allowlist: dict[str, Any], entry: dict[str, Any]) -> str:
    before = render(allowlist).splitlines(keepends=True)
    after = render(draft_allowlist(allowlist, entry)).splitlines(keepends=True)
    header = (
        f"Draft allowlist change for {entry['kind']} {entry['version']} (C14.03). Review it; nothing applied it.\n"
        "Admission also moves RELEASE_ENGINE_PROFILES (Node) and contracts/harness/vectors/engine-profiles.json"
        " (E9 step 3).\n\n"
    )
    diff = difflib.unified_diff(before, after, f"a/{ALLOWLIST_REL}", f"b/{ALLOWLIST_REL}")
    return header + "".join(diff)


def _read_json(path: str) -> Any:  # noqa: ANN401 - any JSON document
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv: list[str] | None = None, *, allowlist_path: Path = ALLOWLIST, out: io.TextIOBase | None = None) -> int:
    stdout = out or sys.stdout
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("sources")
    p.add_argument("--format", choices=("json", "github-matrix"), default="json")
    p = sub.add_parser("decide")
    p.add_argument("--kind", required=True)
    p.add_argument("--tag", required=True)
    p = sub.add_parser("asset-url")
    p.add_argument("--kind", required=True)
    p.add_argument("--version", required=True)
    p = sub.add_parser("binary-digest")
    p.add_argument("--kind", required=True)
    p.add_argument("--asset", required=True)
    p = sub.add_parser("lanes")
    p.add_argument("--kind", required=True)
    p = sub.add_parser("entry")
    for name in ("--kind", "--version", "--image", "--image-digest", "--binary-digest", "--lanes", "--run-url",
                 "--out"):
        p.add_argument(name, required=True)
    p.add_argument("--asset-sha256", default=None)
    p = sub.add_parser("validate")
    p.add_argument("entry")
    p = sub.add_parser("draft-patch")
    p.add_argument("entry")
    p.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    allowlist = load_allowlist(allowlist_path)
    try:
        if args.command == "sources":
            found = sources(allowlist)
            body = {"include": found} if args.format == "github-matrix" else found
            stdout.write(json.dumps(body, separators=(",", ":") if args.format == "github-matrix" else None,
                                    indent=None if args.format == "github-matrix" else 2) + "\n")
        elif args.command == "decide":
            stdout.write(json.dumps(decide(allowlist, args.kind, args.tag)) + "\n")
        elif args.command == "asset-url":
            stdout.write(asset_url(allowlist, args.kind, args.version) + "\n")
        elif args.command == "binary-digest":
            stdout.write(binary_digest(allowlist, args.kind, Path(args.asset)) + "\n")
        elif args.command == "lanes":
            stdout.write(json.dumps(lanes(args.kind)) + "\n")
        elif args.command == "entry":
            entry = make_entry(
                allowlist, kind=args.kind, version=args.version, image=args.image, image_digest=args.image_digest,
                binary_digest=args.binary_digest, asset_sha256=args.asset_sha256 or None,
                lane_results=_read_json(args.lanes), run_url=args.run_url,
            )
            Path(args.out).write_text(json.dumps(entry, indent=2) + "\n", encoding="utf-8")
            stdout.write(f"wrote {args.out}\n")
        elif args.command == "validate":
            errors = validate(_read_json(args.entry), allowlist)
            for error in errors:
                stdout.write(f"{args.entry}: {error}\n")
            return 1 if errors else 0
        elif args.command == "draft-patch":
            Path(args.out).write_text(draft_patch(allowlist, _read_json(args.entry)), encoding="utf-8")
            stdout.write(f"wrote {args.out}\n")
    except CatalogError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
