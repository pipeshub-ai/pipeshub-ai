#!/usr/bin/env python3
"""Licence and binary scan for one engine release (PROJECT-PLAN §4 rule 8, D29).

    licence_scan.py ENGINE VERSION [--network] [--asset PATH --sha256 HEX]
                    [--deps FILE] [--licences FILE] [--manifest FILE] [--signature-bundle PATH]
                    [--allowlist FILE] [--out DIR] [--arch x86_64|aarch64]
                    [--allow-unverified-signature]

Steps, each recorded in the JSON report:

1. Release asset: verified against a pinned sha256 (``--sha256``, or the
   digest GitHub publishes for the asset in ``--network`` mode). A mismatch
   stops the scan.
2. Signature: a sigstore bundle published next to the asset is verified with
   ``cosign`` or ``sigstore`` if one is on PATH; a bundle that cannot be
   verified fails the scan unless ``--allow-unverified-signature``.
3. Dependency licences: Rust from ``cargo tree --format '{p}|{l}'`` output
   (``--deps``; ``--network`` runs it on the tag source, or falls back to
   ``Cargo.lock`` + crates.io), opencode from ``bun.lock`` (``--deps``) with
   licences from the npm registry (``--network``) or ``--licences`` JSON. An
   image's runtime layer (E0b) instead names its components in ``--manifest``
   (PyPI wheels, the native libraries they bundle, distribution packages), each
   with its licence; the asset is then that layer's tarball.
4. Binary markers: every file in the asset is searched for GPL/LGPL/MPL text
   and known copyleft components (R5 §10.2).
5. Policy: permissive licences pass; weak copyleft (MPL-2.0, LGPL) passes with
   a notice and a source pointer (D29); anything else, any unknown licence and
   any undeclared GPL/LGPL marker fails. A licence that is new to the
   allowlist therefore blocks the upgrade. An LGPL crate compiled into a
   static binary (a cargo engine) fails unless it is approved: the user can
   relink it only through the image's notice and source pointer, which a
   new crate does not have yet. A recorded legal approval passes
   one component at one version with exactly the licence it was approved for
   (``reviewed``), or one marker in one member of one engine version
   (``reviewed_markers``), as a notice that cites the approval.

Outputs ``<out>/<engine>-<version>.licence.json`` and
``<out>/<engine>-<version>.NOTICES.md``. Exit code 0 on pass or
pass_with_notices, 1 on fail, 2 on usage errors. Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

if TYPE_CHECKING:
    import io

Json = None | bool | int | float | str | list["Json"] | dict[str, "Json"]
Klass = Literal["permissive", "notice", "blocked", "unknown"]
Fetch = Callable[[str], bytes]

HERE = Path(__file__).resolve().parent
DEFAULT_ALLOWLIST = HERE / "licence_allowlist.json"
CHUNK = 1 << 20

# Text that only appears when (L)GPL code or its notices are compiled in (R5 §10.2).
STRONG_MARKERS = ("GNU General Public License", "GNU GENERAL PUBLIC LICENSE")
WEAK_MARKERS = ("GNU Lesser General Public", "GNU LESSER GENERAL PUBLIC", "GNU Library General Public")
COMPONENT_MARKERS = ("bubblewrap built for Codex", "bwrap_main")
NOTICE_MARKERS = ("Mozilla Public License",)


@dataclass(frozen=True)
class Component:
    """Code an engine embeds that no lockfile lists (e.g. a statically linked runtime)."""

    name: str
    licence: str
    source: str
    note: str
    markers: tuple[str, ...] = ()


@dataclass(frozen=True)
class Engine:
    repo: str
    tag: str
    asset: str
    ecosystem: Literal["cargo", "bun", "npm"]
    source_dir: str
    cargo_package: str = ""
    workspace: str = ""
    embedded: tuple[Component, ...] = ()

    def tag_for(self, version: str) -> str:
        return self.tag.format(version=version)

    def asset_for(self, version: str, arch: str) -> str:
        short = {"x86_64": "x64", "aarch64": "arm64"}[arch]
        return self.asset.format(version=version, arch=arch, arch_short=short)


ENGINES: Mapping[str, Engine] = {
    # Gemini CLI's release bundle (an npm build) is not scanned yet (acp-gemini/licenses/NOTICE.md); its image's
    # runtime layer is, with --manifest (opencode/tools/scan.sh acp-gemini).
    "acp-gemini": Engine(
        repo="google-gemini/gemini-cli",
        tag="v{version}",
        asset="gemini-cli-bundle.zip",
        ecosystem="npm",
        source_dir="",
    ),
    # Proprietary and supplied by the customer's admin (D29): its binary is never scanned or shipped by
    # PipesHub. Only PipesHub's layers of the image are scanned, with --manifest (claude-code/scan.sh).
    "claude_code": Engine(
        repo="anthropics/claude-code",
        tag="v{version}",
        asset="admin-supplied",
        ecosystem="bun",
        source_dir="",
    ),
    "codex": Engine(
        repo="openai/codex",
        tag="rust-v{version}",
        # The standalone app-server only: the -package- archive adds bwrap (LGPL), zsh and rg (R5 §10.2).
        asset="codex-app-server-{arch}-unknown-linux-musl.tar.gz",
        ecosystem="cargo",
        source_dir="codex-rs",
        cargo_package="codex-app-server",
    ),
    # Goose's static musl release binary (WP-76); its image's runtime layer is scanned with --manifest
    # (acp-goose/tools/scan.sh acp-goose).
    "acp-goose": Engine(
        repo="aaif-goose/goose",
        tag="v{version}",
        asset="goose-{arch}-unknown-linux-musl.tar.gz",
        ecosystem="cargo",
        source_dir="",
        cargo_package="goose-cli",
    ),
    # Qwen Code's npm release (WP-91), an esbuild bundle as Gemini CLI's: not scanned yet; its image's runtime layer
    # is, with --manifest (opencode/tools/scan.sh acp-qwen).
    "acp-qwen": Engine(
        repo="QwenLM/qwen-code",
        tag="v{version}",
        asset="qwen-code-{version}.tgz",
        ecosystem="npm",
        source_dir="",
    ),
    # DeepSeek Harness's npm release (WP-105): a Node program over its lock's tree
    # (acp-dsh/engine/package-lock.json, licences tallied in acp-dsh/licenses/NOTICE.md); its image's runtime layer is
    # a Debian image's, scanned with --manifest (acp-dsh/scan.sh).
    "acp-dsh": Engine(
        repo="deepseek-ai/deepseek-harness",
        tag="dsh-v{version}",
        asset="dsh-{version}.tgz",
        ecosystem="npm",
        source_dir="",
    ),
    "opencode": Engine(
        repo="anomalyco/opencode",
        tag="v{version}",
        asset="opencode-linux-{arch_short}.tar.gz",
        ecosystem="bun",
        source_dir="",
        workspace="packages/opencode",
        embedded=(
            Component(
                name="bun runtime (JavaScriptCore/WebKit)",
                licence="LGPL-2.0-only",
                source="https://github.com/oven-sh/WebKit",
                note="Bun statically links JavaScriptCore; relink route per bun.com/docs/project/license (F4 §1.3)",
                markers=WEAK_MARKERS,
            ),
        ),
    ),
}


# ---------------------------------------------------------------- licence expressions


@dataclass(frozen=True)
class Approval:
    by: str
    date: str
    ref: str

    @staticmethod
    def load(raw: Json) -> Approval:
        fields = cast("dict[str, Json]", raw) if isinstance(raw, dict) else {}
        values = [fields.get(k) for k in ("by", "date", "ref")]
        if not all(isinstance(v, str) and v for v in values):
            raise ValueError(f"an approval needs by, date and ref: {raw!r}")
        by, date, ref = cast("list[str]", values)
        return Approval(by, date, ref)

    def cite(self) -> str:
        return f"{self.by}, {self.date} ({self.ref})"


@dataclass(frozen=True)
class Approved:
    """A component approved as found; a wheel-bundled library's "version" names its carrier, hence ``bundled_in``."""

    licence: str
    approval: Approval
    bundled_in: tuple[str, ...] = ()

    def covers(self, package: Package, scanned: frozenset[str]) -> bool:
        return normalise(package.licence) == normalise(self.licence) and all(c in scanned for c in self.bundled_in)


@dataclass(frozen=True)
class ApprovedMarker:
    member: str
    marker: str
    component: str
    licence: str
    source: str
    approval: Approval


def _reviewed_entry(raw: Json) -> str | Approved:
    if isinstance(raw, str):
        return raw
    entry = cast("dict[str, Json]", raw)
    return Approved(
        licence=cast("str", entry["licence"]),
        approval=Approval.load(entry.get("approval")),
        bundled_in=tuple(cast("list[str]", entry.get("bundled_in") or [])),
    )


def _approved_marker(raw: Json) -> ApprovedMarker:
    entry = cast("dict[str, Json]", raw)
    return ApprovedMarker(
        member=cast("str", entry["member"]),
        marker=cast("str", entry["marker"]),
        component=cast("str", entry["component"]),
        licence=cast("str", entry["licence"]),
        source=cast("str", entry["source"]),
        approval=Approval.load(entry.get("approval")),
    )


@dataclass(frozen=True)
class Allowlist:
    permissive: frozenset[str]
    weak_copyleft: frozenset[str]
    # name@version -> the licence a human confirmed (still classified by the lists), or a legal approval as found.
    reviewed: Mapping[str, str | Approved]
    # engine version -> markers approved in that version's asset.
    reviewed_markers: Mapping[str, tuple[ApprovedMarker, ...]] = field(default_factory=dict[str, tuple[ApprovedMarker, ...]])

    @staticmethod
    def load(path: Path, engine: str) -> Allowlist:
        body = cast("dict[str, Json]", json.loads(path.read_text()))
        reviewed = cast("dict[str, dict[str, Json]]", body.get("reviewed") or {})
        # The runtime layer every engine image carries is reviewed once: an approval is of a component at a
        # version, so it holds in every engine's scan; an engine's own entry for the component wins.
        shared = cast("dict[str, Json]", body.get("reviewed_shared") or {})
        markers = cast("dict[str, dict[str, list[Json]]]", body.get("reviewed_markers") or {})
        return Allowlist(
            permissive=frozenset(cast("list[str]", body["permissive"])),
            weak_copyleft=frozenset(cast("list[str]", body["weak_copyleft_with_notice"])),
            reviewed={ident: _reviewed_entry(raw) for ident, raw in {**shared, **reviewed.get(engine, {})}.items()},
            reviewed_markers={
                version: tuple(_approved_marker(raw) for raw in entries) for version, entries in markers.get(engine, {}).items()
            },
        )

    def _canonical(self) -> dict[str, str]:
        return {name.lower(): name for name in self.permissive | self.weak_copyleft}

    def classify_id(self, licence: str) -> tuple[Klass, str]:
        canonical = self._canonical().get(licence.lower(), licence)
        if canonical in self.permissive:
            return "permissive", canonical
        if canonical in self.weak_copyleft:
            return "notice", canonical
        if canonical.upper() in ("UNKNOWN", "NOASSERTION", ""):
            return "unknown", canonical
        return "blocked", canonical


_RANK: Mapping[Klass, int] = {"permissive": 0, "notice": 1, "unknown": 2, "blocked": 3}
_TOKEN = re.compile(r"\(|\)|[^\s()]+")


def normalise(expression: str) -> str:
    """Cargo's legacy ``A/B`` means ``A OR B``."""
    return re.sub(r"\s*/\s*", " OR ", expression.strip())


@dataclass(frozen=True)
class Verdict:
    klass: Klass
    elected: str


def classify(expression: str, allow: Allowlist) -> Verdict:
    """Evaluate an SPDX expression: OR elects the most permissive side, AND needs every side."""
    tokens = _TOKEN.findall(normalise(expression))
    if not tokens:
        return Verdict("unknown", expression)
    position = 0

    def parse_or() -> Verdict:
        nonlocal position
        best = parse_and()
        while position < len(tokens) and tokens[position].upper() == "OR":
            position += 1
            other = parse_and()
            if _RANK[other.klass] < _RANK[best.klass]:
                best = other
        return best

    def parse_and() -> Verdict:
        nonlocal position
        parts = [parse_atom()]
        while position < len(tokens) and tokens[position].upper() == "AND":
            position += 1
            parts.append(parse_atom())
        worst = max(parts, key=lambda v: _RANK[v.klass])
        if len(parts) == 1:
            return worst
        return Verdict(worst.klass, " AND ".join(p.elected for p in parts))

    def parse_atom() -> Verdict:
        nonlocal position
        if position >= len(tokens):
            raise ValueError("unexpected end")
        token = tokens[position]
        position += 1
        if token == "(":
            inner = parse_or()
            if position >= len(tokens) or tokens[position] != ")":
                raise ValueError("unbalanced")
            position += 1
            return inner
        if token.upper() in ("AND", "OR", "WITH", ")"):
            raise ValueError(f"unexpected {token}")
        if position + 1 < len(tokens) and tokens[position].upper() == "WITH":
            exception = tokens[position + 1]
            position += 2
            klass, name = allow.classify_id(f"{token} WITH {exception}")
            if klass in ("permissive", "notice"):
                return Verdict(klass, name)
            # An exception only relaxes the base licence, so the base decides.
            klass, name = allow.classify_id(token)
            return Verdict(klass, f"{name} WITH {exception}")
        klass, name = allow.classify_id(token.rstrip("+"))
        return Verdict(klass, name)

    try:
        verdict = parse_or()
    except ValueError:
        return Verdict("unknown", expression)
    if position != len(tokens):
        return Verdict("unknown", expression)
    return verdict


# ---------------------------------------------------------------- dependencies


@dataclass(frozen=True)
class Package:
    name: str
    version: str
    licence: str
    ecosystem: Literal["cargo", "npm", "pypi", "apk", "native", "runtime"]
    url: str = ""

    @property
    def ident(self) -> str:
        return f"{self.name}@{self.version}"

    @property
    def source(self) -> str:
        if self.url:
            return self.url
        if self.ecosystem == "cargo":
            return f"https://crates.io/crates/{self.name}/{self.version}"
        if self.ecosystem == "pypi":
            return f"https://pypi.org/project/{self.name}/{self.version}/"
        return f"https://www.npmjs.com/package/{self.name}/v/{self.version}"


@dataclass(frozen=True)
class Manifest:
    """``--manifest``: the components of an image layer, and the marker texts they are known to carry."""

    packages: tuple[Package, ...]
    declared_markers: frozenset[str]

    @staticmethod
    def load(path: Path) -> Manifest:
        body = cast("dict[str, Json]", json.loads(path.read_text()))
        packages: list[Package] = []
        markers: set[str] = set()
        for raw in cast("list[dict[str, Json]]", body["components"]):
            packages.append(
                Package(
                    name=cast("str", raw["name"]),
                    version=cast("str", raw["version"]),
                    licence=cast("str", raw["licence"]),
                    ecosystem=cast("Literal['pypi', 'apk', 'native', 'runtime']", raw["ecosystem"]),
                    url=cast("str", raw.get("source") or ""),
                )
            )
            markers.update(cast("list[str]", raw.get("markers") or []))
        return Manifest(tuple(packages), frozenset(markers))


_CARGO_LINE = re.compile(r"^(?P<name>[A-Za-z0-9_.-]+) v(?P<version>[^\s|]+)(?: \([^)]*\))?\|(?P<licence>.*)$")


def parse_cargo_tree(text: str) -> list[Package]:
    """Lines of ``cargo tree -e normal,build --prefix none --format '{p}|{l}'``; duplicates dropped."""
    seen: dict[str, Package] = {}
    for line in text.splitlines():
        match = _CARGO_LINE.match(line.strip().removesuffix(" (*)"))
        if match is None:
            continue
        package = Package(match["name"], match["version"], match["licence"].strip() or "UNKNOWN", "cargo")
        seen.setdefault(package.ident, package)
    return list(seen.values())


def parse_cargo_lock(text: str) -> list[tuple[str, str]]:
    """``(name, version)`` of every registry package in a Cargo.lock (over-approximates the build)."""
    packages: list[tuple[str, str]] = []
    for block in text.split("[[package]]")[1:]:
        name = re.search(r'^name = "([^"]+)"', block, re.MULTILINE)
        version = re.search(r'^version = "([^"]+)"', block, re.MULTILINE)
        registry = re.search(r'^source = "registry\+', block, re.MULTILINE)
        if name and version and registry:
            packages.append((name[1], version[1]))
    return packages


def _first_object(values: Sequence[Json]) -> dict[str, Json]:
    for value in values:
        if isinstance(value, dict):
            return value
    return {}


def bun_closure(lock_text: str, workspace: str) -> list[str]:
    """``name@version`` reachable from ``workspace`` through dependencies and workspace links."""
    lock = cast("dict[str, Json]", json.loads(re.sub(r",(\s*[}\]])", r"\1", lock_text)))
    workspaces = cast("dict[str, dict[str, Json]]", lock["workspaces"])
    packages = cast("dict[str, list[Json]]", lock["packages"])
    by_name = {cast("str", w.get("name")): key for key, w in workspaces.items()}
    seen: set[str] = set()
    seen_ws: set[str] = set()
    todo: list[tuple[str, str]] = []

    def deps(entry: Mapping[str, Json]) -> Iterator[tuple[str, str]]:
        for section in ("dependencies", "optionalDependencies"):
            for dep, spec in cast("dict[str, Json]", entry.get(section) or {}).items():
                yield dep, str(spec)

    def add_workspace(key: str) -> None:
        if key in seen_ws:
            return
        seen_ws.add(key)
        for dep, spec in deps(workspaces[key]):
            if spec.startswith("workspace:") and dep in by_name:
                add_workspace(by_name[dep])
            else:
                todo.append(("", dep))

    def resolve(parent: str, name: str) -> str | None:
        segments: list[str] = []
        parts = parent.split("/") if parent else []
        index = 0
        while index < len(parts):
            if parts[index].startswith("@") and index + 1 < len(parts):
                segments.append(parts[index] + "/" + parts[index + 1])
                index += 2
            else:
                segments.append(parts[index])
                index += 1
        for cut in range(len(segments), 0, -1):
            nested = "/".join(segments[:cut]) + "/" + name
            if nested in packages:
                return nested
        return name if name in packages else None

    add_workspace(workspace)
    while todo:
        parent, name = todo.pop()
        key = resolve(parent, name)
        if key is None or key in seen:
            continue
        seen.add(key)
        entry = packages[key]
        ident = cast("str", entry[0])
        if "@workspace:" in ident:
            linked = ident.rsplit("@workspace:", 1)[0]
            if linked in by_name:
                add_workspace(by_name[linked])
            continue
        meta = _first_object(entry[1:])
        todo.extend((key, dep) for dep, _ in deps(meta))
    return sorted(
        {cast("str", packages[k][0]) for k in seen if "@workspace:" not in cast("str", packages[k][0]) and "@file:" not in cast("str", packages[k][0])}
    )


def npm_licence(fetch: Fetch, ident: str) -> str:
    name, version = ident.rsplit("@", 1)
    if version.startswith(("github:", "git", "http", "file:", "link:")):
        return "UNKNOWN"
    body = cast("dict[str, Json]", json.loads(fetch(f"https://registry.npmjs.org/{urllib.parse.quote(name, safe='@')}/{version}")))
    value = body.get("license") or body.get("licenses")
    if isinstance(value, dict):
        value = value.get("type")
    if isinstance(value, list):
        value = " OR ".join(str(v.get("type") if isinstance(v, dict) else v) for v in value)
    return str(value) if value else "UNKNOWN"


def crate_licence(fetch: Fetch, name: str, version: str) -> str:
    body = cast("dict[str, Json]", json.loads(fetch(f"https://crates.io/api/v1/crates/{name}/{version}")))
    return str(cast("dict[str, Json]", body.get("version") or {}).get("license") or "UNKNOWN")


# ---------------------------------------------------------------- asset, markers, signature


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _members(path: Path) -> Iterator[tuple[str, io.BufferedIOBase]]:
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if not info.is_dir():
                    yield info.filename, cast("io.BufferedIOBase", archive.open(info))
        return
    try:
        archive = tarfile.open(path, mode="r:*")
    except tarfile.ReadError:
        with path.open("rb") as handle:
            yield path.name, cast("io.BufferedIOBase", handle)
        return
    with archive:
        for member in archive:
            extracted = archive.extractfile(member) if member.isfile() else None
            if extracted is not None:
                yield member.name, cast("io.BufferedIOBase", extracted)


def scan_markers(path: Path, markers: Sequence[str]) -> dict[str, dict[str, int]]:
    """``{member: {marker: hits}}``; streamed in chunks with an overlap so no hit is split."""
    encoded = [(m, m.encode()) for m in markers]
    overlap = max(len(b) for _, b in encoded) - 1
    found: dict[str, dict[str, int]] = {}
    for name, handle in _members(path):
        tail = b""
        counts: dict[str, int] = {}
        while chunk := handle.read(CHUNK):
            window = tail + chunk
            for marker, needle in encoded:
                hits = window.count(needle) - tail.count(needle)
                if hits:
                    counts[marker] = counts.get(marker, 0) + hits
            tail = window[-overlap:] if overlap else b""
        if counts:
            found[name] = counts
    return found


SignatureStatus = Literal["verified", "not_published", "not_checked", "verifier_missing", "failed"]


GITHUB_OIDC_ISSUER = "https://token.actions.githubusercontent.com"


def verify_signature(
    asset: Path, bundle: Path | None, repo: str, *, which: Callable[[str], str | None] = shutil.which
) -> SignatureStatus:
    if bundle is None:
        return "not_published"
    cosign, sigstore = which("cosign"), which("sigstore")
    # Any Fulcio certificate verifies under a wildcard identity, so the signer must be the engine's own release workflow.
    if cosign is not None:
        command = [cosign, "verify-blob", "--bundle", str(bundle),
                   "--certificate-identity-regexp", f"^https://github\\.com/{re.escape(repo)}/",
                   "--certificate-oidc-issuer", GITHUB_OIDC_ISSUER, str(asset)]
    elif sigstore is not None:
        command = [sigstore, "verify", "github", "--bundle", str(bundle), "--repository", repo, str(asset)]
    else:
        return "verifier_missing"
    if subprocess.run(command, capture_output=True, check=False).returncode == 0:
        return "verified"
    # Some releases sign the binary inside the archive rather than the archive itself.
    with tempfile.TemporaryDirectory(prefix="engine-sig-") as tmp:
        for member, handle in _members(asset):
            target = Path(tmp) / Path(member).name
            with target.open("wb") as out:
                shutil.copyfileobj(handle, out, CHUNK)
            retry = [*command[:-1], str(target)]
            if subprocess.run(retry, capture_output=True, check=False).returncode == 0:
                return "verified"
    return "failed"


# ---------------------------------------------------------------- report


@dataclass
class Report:
    engine: str
    version: str
    asset: str
    sha256: str = ""
    expected_sha256: str = ""
    signature: SignatureStatus = "not_checked"
    dependency_source: str = ""
    packages: int = 0
    licences: dict[str, int] = field(default_factory=dict[str, int])
    notices: list[dict[str, str]] = field(default_factory=list[dict[str, str]])
    blocked: list[dict[str, str]] = field(default_factory=list[dict[str, str]])
    elections: list[dict[str, str]] = field(default_factory=list[dict[str, str]])
    markers: dict[str, dict[str, int]] = field(default_factory=dict[str, dict[str, int]])
    marker_failures: list[str] = field(default_factory=list[str])
    failures: list[str] = field(default_factory=list[str])
    verdict: Literal["pass", "pass_with_notices", "fail"] = "fail"

    def finish(self) -> Report:
        if self.failures or self.blocked or self.marker_failures:
            self.verdict = "fail"
        elif self.notices:
            self.verdict = "pass_with_notices"
        else:
            self.verdict = "pass"
        return self


def apply_policy(report: Report, packages: Iterable[Package], allow: Allowlist) -> None:
    ordered = sorted(packages, key=lambda p: p.ident)
    scanned = frozenset(p.ident for p in ordered)
    for package in ordered:
        report.packages += 1
        report.licences[package.licence] = report.licences.get(package.licence, 0) + 1
        reviewed = allow.reviewed.get(package.ident)
        if isinstance(reviewed, Approved):
            if reviewed.covers(package, scanned):
                report.notices.append(
                    {"package": package.ident, "licence": package.licence, "source": package.source, "kind": "dependency",
                     "approval": reviewed.approval.cite()}
                )
                continue
            # Approved for another licence or carrier version: a new finding, so the lists decide.
            reviewed = None
        verdict = classify(reviewed if reviewed is not None else package.licence, allow)
        if verdict.klass in ("blocked", "unknown"):
            report.blocked.append({"package": package.ident, "licence": package.licence, "reason": verdict.klass})
            continue
        if package.ecosystem == "cargo" and verdict.klass == "notice" and "LGPL" in verdict.elected.upper():
            report.blocked.append({"package": package.ident, "licence": package.licence, "reason": "static_lgpl_unapproved"})
            continue
        if normalise(package.licence) != verdict.elected:
            report.elections.append({"package": package.ident, "licence": package.licence, "elected": verdict.elected})
        if verdict.klass == "notice":
            report.notices.append(
                {"package": package.ident, "licence": verdict.elected, "source": package.source, "kind": "dependency"}
            )


def judge_markers(
    report: Report, engine: Engine, also_declared: frozenset[str] = frozenset(), approved: Sequence[ApprovedMarker] = ()
) -> None:
    declared = {m for c in engine.embedded for m in c.markers} | also_declared
    by_hit = {(a.member, a.marker): a for a in approved}
    cited: set[str] = set()
    for member, counts in report.markers.items():
        for marker in counts:
            if marker in NOTICE_MARKERS or marker in declared:
                continue
            hit = by_hit.get((member, marker))
            if hit is None:
                report.marker_failures.append(f"{member}: {marker}")
            elif hit.component not in cited:
                cited.add(hit.component)
                report.notices.append(
                    {"package": hit.component, "licence": hit.licence, "source": hit.source, "kind": "embedded",
                     "note": f"{marker!r} in {member}", "approval": hit.approval.cite()}
                )
    for component in engine.embedded:
        report.notices.append(
            {"package": component.name, "licence": component.licence, "source": component.source, "kind": "embedded", "note": component.note}
        )


def write_outputs(report: Report, out: Path) -> tuple[Path, Path]:
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{report.engine}-{report.version}"
    json_path = out / f"{stem}.licence.json"
    json_path.write_text(json.dumps(asdict(report), indent=1, sort_keys=True) + "\n")
    lines = [
        f"# Third-party notices: {report.engine} {report.version}",
        "",
        "Weak-copyleft code (D29), and components legal approved for this version, shipped unmodified inside",
        "this engine. Their source is available at the pointers below; the copyleft does not extend to other code.",
        "",
        "| Component | Licence | Source | Approval |",
        "|---|---|---|---|",
    ]
    lines.extend(f"| {n['package']} | {n['licence']} | {n['source']} | {n.get('approval', '')} |" for n in report.notices)
    notices_path = out / f"{stem}.NOTICES.md"
    notices_path.write_text("\n".join(lines) + "\n")
    return json_path, notices_path


# ---------------------------------------------------------------- network mode


def http_get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"user-agent": "engine-licence-scan"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return cast("bytes", response.read())


def _release(fetch: Fetch, engine: Engine, version: str) -> dict[str, Json]:
    url = f"https://api.github.com/repos/{engine.repo}/releases/tags/{urllib.parse.quote(engine.tag_for(version))}"
    return cast("dict[str, Json]", json.loads(fetch(url)))


def _download(url: str, dest: Path) -> None:
    request = urllib.request.Request(url, headers={"user-agent": "engine-licence-scan"})
    with urllib.request.urlopen(request, timeout=600) as response, dest.open("wb") as handle:
        shutil.copyfileobj(response, handle, CHUNK)


def bundle_names(asset: str) -> list[str]:
    """Where a release may publish the asset's sigstore bundle; Codex names it after the binary."""
    stem = re.sub(r"\.(tar\.gz|tar\.zst|tgz|zst|zip)$", "", asset)
    return [f"{base}{suffix}" for base in (asset, stem) for suffix in (".sigstore", ".sigstore.json", ".bundle")]


def network_inputs(args: argparse.Namespace, engine: Engine, work: Path, fetch: Fetch) -> tuple[Path, str, Path | None, list[Package], str]:
    release = _release(fetch, engine, args.version)
    assets = {cast("str", a["name"]): a for a in cast("list[dict[str, Json]]", release.get("assets") or [])}
    # The asset a release ships may be another build of the version (E0 ships the musl build).
    name = Path(args.asset).name if args.asset else engine.asset_for(args.version, args.arch)
    if name not in assets:
        raise SystemExit(f"release has no asset {name}")
    asset = Path(args.asset) if args.asset else work / name
    if not args.asset:
        _download(cast("str", assets[name]["browser_download_url"]), asset)
    expected = args.sha256 or cast("str", assets[name].get("digest") or "").removeprefix("sha256:")
    bundle: Path | None = None
    for candidate in bundle_names(name):
        if candidate in assets:
            bundle = work / candidate
            _download(cast("str", assets[candidate]["browser_download_url"]), bundle)
            break
    source = work / "source.tar.gz"
    _download(f"https://github.com/{engine.repo}/archive/refs/tags/{engine.tag_for(args.version)}.tar.gz", source)
    with tarfile.open(source) as archive:
        archive.extractall(work / "src", filter="data")
    root = next((work / "src").iterdir())
    if engine.ecosystem == "cargo":
        packages, how = _cargo_packages(root / engine.source_dir, engine, args.arch, fetch)
    else:
        idents = bun_closure((root / "bun.lock").read_text(), engine.workspace)
        packages = [_npm_package(i, npm_licence(fetch, i)) for i in idents]
        how = "bun.lock closure + npm registry (over-approximates the bundle)"
    return asset, expected, bundle, packages, how


def _cargo_packages(root: Path, engine: Engine, arch: str, fetch: Fetch) -> tuple[list[Package], str]:
    cargo = shutil.which("cargo")
    if cargo is not None:
        # Release tarballs stamp the workspace version into Cargo.toml but not Cargo.lock, so
        # --locked refuses; cargo may rewrite only workspace entries, never registry ones.
        locked = parse_cargo_lock((root / "Cargo.lock").read_text())
        tree = subprocess.run(
            [cargo, "tree", "-p", engine.cargo_package, "-e", "normal,build", "--target", f"{arch}-unknown-linux-musl",
             "--prefix", "none", "--format", "{p}|{l}"],
            cwd=root, capture_output=True, text=True, check=False,
        )
        if tree.returncode != 0:
            raise SystemExit(f"cargo tree failed: {tree.stderr.strip()[-2000:]}")
        if parse_cargo_lock((root / "Cargo.lock").read_text()) != locked:
            raise SystemExit("cargo changed registry packages in Cargo.lock; the scan would not match the release")
        return parse_cargo_tree(tree.stdout), "cargo tree (build closure for the target)"
    lock = parse_cargo_lock((root / "Cargo.lock").read_text())
    packages = [Package(n, v, crate_licence(fetch, n, v), "cargo") for n, v in lock]
    return packages, "Cargo.lock + crates.io (no cargo on PATH: over-approximates the build)"


def _npm_package(ident: str, licence: str) -> Package:
    name, version = ident.rsplit("@", 1)
    return Package(name, version, licence, "npm")


# ---------------------------------------------------------------- main


def offline_packages(args: argparse.Namespace, engine: Engine) -> tuple[list[Package], str]:
    if args.manifest is not None:
        return list(Manifest.load(Path(args.manifest)).packages), f"layer manifest ({Path(args.manifest).name})"
    if args.deps is None:
        raise SystemExit("--deps is required without --network")
    text = Path(args.deps).read_text()
    if engine.ecosystem == "cargo":
        return parse_cargo_tree(text), f"cargo tree output ({Path(args.deps).name})"
    licences = cast("dict[str, str]", json.loads(Path(args.licences).read_text())) if args.licences else {}
    idents = bun_closure(text, engine.workspace)
    return [_npm_package(i, licences.get(i, "UNKNOWN")) for i in idents], f"bun.lock closure ({Path(args.deps).name})"


def run(argv: Sequence[str], *, fetch: Fetch = http_get, which: Callable[[str], str | None] = shutil.which) -> Report:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("engine", choices=sorted(ENGINES))
    parser.add_argument("version")
    parser.add_argument("--network", action="store_true")
    parser.add_argument("--arch", default="x86_64", choices=("x86_64", "aarch64"))
    parser.add_argument("--asset")
    parser.add_argument("--sha256")
    parser.add_argument("--signature-bundle")
    parser.add_argument("--deps")
    parser.add_argument("--licences")
    parser.add_argument("--manifest")
    parser.add_argument("--allowlist", default=str(DEFAULT_ALLOWLIST))
    parser.add_argument("--out", default=".")
    parser.add_argument("--allow-unverified-signature", action="store_true")
    args = parser.parse_args(argv)
    engine = ENGINES[args.engine]
    if engine.ecosystem == "npm" and args.manifest is None:
        raise SystemExit(f"{args.engine}: only an image layer with --manifest can be scanned")
    allow = Allowlist.load(Path(args.allowlist), args.engine)
    with tempfile.TemporaryDirectory(prefix="engine-licence-") as tmp:
        if args.network:
            asset, expected, bundle, packages, how = network_inputs(args, engine, Path(tmp), fetch)
        else:
            if args.asset is None or args.sha256 is None:
                raise SystemExit("--asset and --sha256 are required without --network")
            asset, expected = Path(args.asset), cast("str", args.sha256)
            bundle = Path(args.signature_bundle) if args.signature_bundle else None
            packages, how = offline_packages(args, engine)
        report = Report(engine=args.engine, version=args.version, asset=asset.name, expected_sha256=expected)
        report.sha256 = sha256_of(asset)
        if not expected:
            report.failures.append("no pinned sha256 for the asset")
            return report.finish()
        if report.sha256 != expected.lower():
            report.failures.append("asset sha256 does not match the pin")
            return report.finish()
        report.signature = verify_signature(asset, bundle, engine.repo, which=which)
        if report.signature == "failed" or (
            report.signature == "verifier_missing" and not args.allow_unverified_signature
        ):
            report.failures.append(f"signature {report.signature}")
        report.dependency_source = how
        apply_policy(report, packages, allow)
        report.markers = scan_markers(asset, (*STRONG_MARKERS, *WEAK_MARKERS, *COMPONENT_MARKERS, *NOTICE_MARKERS))
        approved = allow.reviewed_markers.get(args.version, ())
        if args.manifest:
            # A runtime layer is not the engine's asset: its own manifest declares what it embeds.
            judge_markers(report, replace(engine, embedded=()), Manifest.load(Path(args.manifest)).declared_markers, approved)
        else:
            judge_markers(report, engine, approved=approved)
        report.finish()
        write_outputs(report, Path(args.out))
        return report


def main(argv: Sequence[str] | None = None) -> int:
    report = run(sys.argv[1:] if argv is None else argv)
    summary = {k: v for k, v in asdict(report).items() if k in ("engine", "version", "verdict", "signature", "packages", "failures", "marker_failures")}
    summary["blocked"] = len(report.blocked)
    summary["notices"] = len(report.notices)
    sys.stdout.write(json.dumps(summary, indent=1) + "\n")
    return 0 if report.verdict != "fail" else 1


if __name__ == "__main__":
    raise SystemExit(main())
