"""`SkillPackageImporter` — turns an npm package, a direct archive URL, or an
uploaded zip/tarball into a normalized, NOT-YET-PERSISTED `ImportPreview`
(name/description/content/resources/warnings).

Design (stateless preview/confirm, deliberately no server-side staging):
every `preview_*` method is a pure fetch-and-parse with no write to the
graph. The REST layer (`api/routes/skills.py`) shows the user this preview,
and a SEPARATE, source-agnostic `finalize()` call — fed the exact
`content`/`resources` the preview already returned — does the actual
`SkillManager.create()` + `write_resource()` persistence. This avoids any
server-side staging-table/TTL-cache statefulness (which would need to be
sticky-session or Redis-backed to work across multiple query-service
instances) at the cost of the client round-tripping the (typically small,
KB-sized) SKILL.md text + resource contents it already received back on
confirm — an explicit, deliberate trade documented here so it isn't
"rediscovered" as a bug later.

Binary resource files (anything that isn't valid UTF-8 text) are skipped
with a warning rather than persisted — `agentSkills.resourceContents` is a
graph-doc string field (see `graph_store.py`), not a blob store; the plan's
Phase 3 blob-storage offload (`blob_resources` in the Node.js gateway) is
the intended home for binary bundled resources, wired at the Node.js layer
since only it holds the `StorageService`.
"""

from __future__ import annotations

import io
import re
import tarfile
import zipfile
from dataclasses import dataclass, field

import httpx

from app.agent_loop_lib.modules.providers.skills.loader import parse_skill_md
from app.agent_loop_lib.modules.providers.skills.validator import SkillFormatError, SkillValidator
from app.services.skills.npm_command_parser import PackageSpec

__all__ = [
    "ImportPreview",
    "PackageImportError",
    "SkillPackageImporter",
]

_RESOURCE_KINDS = ("scripts", "references", "assets")
_NPM_REGISTRY_BASE = "https://registry.npmjs.org"
_MAX_ARCHIVE_BYTES = 25 * 1024 * 1024  # 25MB — a skill pack is markdown + small scripts, not a model checkpoint
_MAX_ARCHIVE_MEMBERS = 500


class PackageImportError(ValueError):
    """Raised for any fetch/extract/parse failure — always safe to show
    `str(exc)` directly to the end user (no internal details leak in)."""


@dataclass
class ImportPreview:
    name: str
    description: str
    version: str
    content: str
    resources: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    skipped_binary_resources: list[str] = field(default_factory=list)
    source_label: str = ""  # e.g. "npm:@acme/skill-pack@1.2.0", surfaced to the user for confirmation


def _decode_text(data: bytes) -> str | None:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _strip_common_prefix(paths: list[str]) -> str:
    """npm tarballs always nest everything under `package/`; generic zip/tar
    archives often nest under a single `<repo-name>-<sha>/` directory. Detect
    and strip exactly one shared leading path segment, if all entries share
    one — never strips a segment that isn't common to every file, so a flat
    archive is left untouched."""
    segments = {p.split("/", 1)[0] for p in paths if "/" in p}
    if len(segments) == 1 and all(p.startswith(next(iter(segments)) + "/") for p in paths):
        return next(iter(segments)) + "/"
    return ""


def _extract_zip(data: bytes) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = zf.infolist()
            if len(infos) > _MAX_ARCHIVE_MEMBERS:
                raise PackageImportError(f"Archive has too many files ({len(infos)} > {_MAX_ARCHIVE_MEMBERS}).")
            for info in infos:
                if info.is_dir():
                    continue
                _reject_unsafe_path(info.filename)
                files[info.filename] = zf.read(info)
    except zipfile.BadZipFile as e:
        raise PackageImportError(f"Not a valid zip archive: {e}") from e
    return files


def _extract_tar(data: bytes) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:
            members = tf.getmembers()
            if len(members) > _MAX_ARCHIVE_MEMBERS:
                raise PackageImportError(f"Archive has too many files ({len(members)} > {_MAX_ARCHIVE_MEMBERS}).")
            for member in members:
                if not member.isfile():
                    continue
                _reject_unsafe_path(member.name)
                extracted = tf.extractfile(member)
                if extracted is not None:
                    files[member.name] = extracted.read()
    except tarfile.TarError as e:
        raise PackageImportError(f"Not a valid tar/tgz archive: {e}") from e
    return files


def _reject_unsafe_path(path: str) -> None:
    """Zip-slip guard: reject absolute paths and any `..` traversal segment
    before a single byte is written/kept in memory."""
    if path.startswith("/") or path.startswith("\\") or re.search(r"(^|/)\.\.(/|$)", path):
        raise PackageImportError(f"Archive contains an unsafe path: {path!r}")


def _files_to_preview(files: dict[str, bytes], *, source_label: str) -> ImportPreview:
    if not files:
        raise PackageImportError("Archive is empty.")

    prefix = _strip_common_prefix(list(files))
    stripped = {(p[len(prefix):] if prefix else p): content for p, content in files.items()}

    skill_md_path = next((p for p in stripped if p == "SKILL.md" or p.endswith("/SKILL.md")), None)
    if skill_md_path is None:
        raise PackageImportError(
            "No SKILL.md found in the archive. Skills must include a SKILL.md file "
            "at the root (see agentskills.io/specification)."
        )
    # A SKILL.md nested one level deeper (e.g. "my-skill/SKILL.md" inside an
    # already-stripped archive) means resource paths need that same prefix
    # stripped too, so 'scripts/foo.sh' resolves relative to SKILL.md, not the archive root.
    skill_dir_prefix = skill_md_path[: -len("SKILL.md")]

    raw_content = _decode_text(stripped[skill_md_path])
    if raw_content is None:
        raise PackageImportError("SKILL.md is not valid UTF-8 text.")

    validator = SkillValidator()
    try:
        skill = parse_skill_md(raw_content, expected_name=None, validator=validator)
    except SkillFormatError as e:
        raise PackageImportError(f"Invalid SKILL.md: {e}") from e

    resources: dict[str, str] = {}
    skipped: list[str] = []
    for path, data in stripped.items():
        if path == skill_md_path or not path.startswith(skill_dir_prefix):
            continue
        rel = path[len(skill_dir_prefix):]
        kind = rel.split("/", 1)[0]
        if kind not in _RESOURCE_KINDS:
            continue
        text = _decode_text(data)
        if text is None:
            skipped.append(rel)
            continue
        resources[rel] = text

    warnings = [w.message for w in validator.lint(skill)]
    if skipped:
        warnings.append(
            f"{len(skipped)} bundled resource file(s) are binary and were not imported "
            f"(text-only resources are supported today): {', '.join(sorted(skipped)[:5])}"
            + ("…" if len(skipped) > 5 else "")
        )

    return ImportPreview(
        name=skill.metadata.name,
        description=skill.metadata.description,
        version=skill.metadata.version,
        content=raw_content,
        resources=resources,
        warnings=warnings,
        skipped_binary_resources=skipped,
        source_label=source_label,
    )


class SkillPackageImporter:
    """Stateless fetch+parse for all three import sources — see module
    docstring for why there's no `confirm(staging_id)`; callers persist the
    returned `ImportPreview.content`/`.resources` directly via
    `SkillManager.create`/`write_resource`."""

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._client = http_client

    async def _get_client(self) -> httpx.AsyncClient:
        return self._client or httpx.AsyncClient(timeout=15.0, follow_redirects=True)

    async def preview_npm(self, spec: PackageSpec) -> ImportPreview:
        owns_client = self._client is None
        client = await self._get_client()
        try:
            meta_url = f"{_NPM_REGISTRY_BASE}/{spec.name}/{spec.version}"
            resp = await client.get(meta_url)
            if resp.status_code == 404:
                raise PackageImportError(
                    f"Package {spec.registry_spec!r} was not found on the npm registry."
                )
            resp.raise_for_status()
            manifest = resp.json()
            tarball_url = (manifest.get("dist") or {}).get("tarball")
            resolved_version = manifest.get("version") or spec.version
            if not tarball_url:
                raise PackageImportError(f"npm registry entry for {spec.name!r} has no downloadable tarball.")

            tarball_resp = await client.get(tarball_url)
            tarball_resp.raise_for_status()
            data = tarball_resp.content
            if len(data) > _MAX_ARCHIVE_BYTES:
                raise PackageImportError(f"Package tarball is too large ({len(data)} bytes).")

            files = _extract_tar(data)
            return _files_to_preview(files, source_label=f"npm:{spec.name}@{resolved_version}")
        except httpx.HTTPError as e:
            raise PackageImportError(f"Failed to fetch {spec.name!r} from the npm registry: {e}") from e
        finally:
            if owns_client:
                await client.aclose()

    async def preview_url(self, url: str) -> ImportPreview:
        if not url.lower().startswith(("https://", "http://")):
            raise PackageImportError("Only http(s) URLs are supported.")
        owns_client = self._client is None
        client = await self._get_client()
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.content
            if len(data) > _MAX_ARCHIVE_BYTES:
                raise PackageImportError(f"Archive is too large ({len(data)} bytes).")
            content_type = resp.headers.get("content-type", "")
            files = self._extract_by_hint(data, url=url, content_type=content_type)
            return _files_to_preview(files, source_label=f"url:{url}")
        except httpx.HTTPError as e:
            raise PackageImportError(f"Failed to fetch {url!r}: {e}") from e
        finally:
            if owns_client:
                await client.aclose()

    def preview_upload(self, filename: str, data: bytes) -> ImportPreview:
        if len(data) > _MAX_ARCHIVE_BYTES:
            raise PackageImportError(f"Uploaded file is too large ({len(data)} bytes).")
        files = self._extract_by_hint(data, url=filename, content_type="")
        return _files_to_preview(files, source_label=f"upload:{filename}")

    @staticmethod
    def _extract_by_hint(data: bytes, *, url: str, content_type: str) -> dict[str, bytes]:
        lowered = url.lower()
        is_zip = lowered.endswith(".zip") or "zip" in content_type
        is_tar = lowered.endswith((".tar", ".tgz", ".tar.gz")) or "tar" in content_type or "gzip" in content_type
        if is_zip and not is_tar:
            return _extract_zip(data)
        if is_tar and not is_zip:
            return _extract_tar(data)
        # Ambiguous/no hint (e.g. a bare download URL with no extension) —
        # sniff by magic bytes rather than guessing from an unreliable name.
        if data[:4] == b"PK\x03\x04":
            return _extract_zip(data)
        try:
            return _extract_tar(data)
        except PackageImportError:
            return _extract_zip(data)
