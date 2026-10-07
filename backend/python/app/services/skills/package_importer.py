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
import json
import re
import tarfile
import zipfile
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import IO, Any

from pydantic import BaseModel, ValidationError

from app.agent_loop_lib.modules.providers.skills.loader import parse_skill_md
from app.agent_loop_lib.modules.providers.skills.validator import SkillFormatError, SkillValidator
from app.services.skills.npm_command_parser import PackageSpec
from app.utils.logger import create_logger
from app.utils.public_http import (
    PublicFetchError,
    PublicFetchLimits,
    PublicFetchResponse,
    PublicUrlFetcher,
    ResponseTooLargeError,
    UnsafeUrlError,
)
from app.utils.url_redaction import redact_url

__all__ = [
    "ImportPreview",
    "PackageImportError",
    "SkillPackageImporter",
]

logger = create_logger(__name__)

_MIB = 1024 * 1024
# Directories/files never imported as bundled resources, on top of the
# archive-wide zip-slip guard in `_reject_unsafe_path`. Not restricted to
# `scripts/`/`references/`/`assets/` — the agentskills.io spec allows "any
# additional files or directories" (real community skills rely on this,
# e.g. Anthropic's `docx` skill ships an `ooxml/` directory of scripts and
# schemas alongside a root-level `ooxml.md`), so anything under the skill
# directory is a candidate resource except these.
_IGNORED_RESOURCE_DIR_NAMES = ("__pycache__", "node_modules", ".git")
_NPM_REGISTRY_BASE = "https://registry.npmjs.org"

# GitHub repo page → downloadable archive. Codeload serves a tarball for a
# ref without an authenticated API hop; HEAD is GitHub's default-branch
# alias. Trailing `.git` / slash / `#ref` are normalized; tree/blob/archive
# paths are left alone so those can go through the Contents API instead.
_GITHUB_REPO_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[A-Za-z0-9._-]+)/(?P<repo>[A-Za-z0-9._-]+?)"
    r"(?:\.git)?/?(?:#(?P<ref>[A-Za-z0-9._/-]+))?$",
)
_GITHUB_TREE_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[A-Za-z0-9._-]+)/(?P<repo>[A-Za-z0-9._-]+?)"
    r"(?:\.git)?/tree/(?P<ref>[^/#]+)/(?P<subpath>.+?)/?$",
)
_GITHUB_OWNER_REPO_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[A-Za-z0-9._-]+)/(?P<repo>[A-Za-z0-9._-]+)",
)
_CATALOG_INSTALL_URL = "https://www.openagentskill.com/api/skills/{slug}/install"
_MAX_MANIFEST_BYTES = 5 * _MIB
_MAX_ARCHIVE_BYTES = 25 * _MIB  # a skill pack is markdown + small scripts, not a model checkpoint
_MAX_EXTRACTED_BYTES = 4 * _MAX_ARCHIVE_BYTES
_MAX_ARCHIVE_MEMBERS = 500
_READ_CHUNK_BYTES = 64 * 1024
_MANIFEST_LIMITS = PublicFetchLimits(max_bytes=_MAX_MANIFEST_BYTES)
_ARCHIVE_LIMITS = PublicFetchLimits(max_bytes=_MAX_ARCHIVE_BYTES)
_UNSAFE_URL_MESSAGE = "This URL is not allowed: it must point to a public address."


class _NpmDist(BaseModel):
    tarball: str | None = None


class _NpmVersionManifest(BaseModel):
    """The fields we use from `GET registry.npmjs.org/<name>/<version>`."""

    version: str | None = None
    dist: _NpmDist = _NpmDist()


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


def _read_bounded(fileobj: IO[bytes], remaining: int) -> bytes:
    """Read one archive member, failing once the extraction budget is spent. Counts the
    bytes actually produced, because the sizes an archive declares can lie."""
    chunks: list[bytes] = []
    received = 0
    while chunk := fileobj.read(_READ_CHUNK_BYTES):
        received += len(chunk)
        if received > remaining:
            raise PackageImportError(
                f"Archive is too large once extracted (limit {_MAX_EXTRACTED_BYTES // _MIB} MB)."
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _extract_zip(data: bytes) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    budget = _MAX_EXTRACTED_BYTES
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = zf.infolist()
            if len(infos) > _MAX_ARCHIVE_MEMBERS:
                raise PackageImportError(f"Archive has too many files ({len(infos)} > {_MAX_ARCHIVE_MEMBERS}).")
            for info in infos:
                if info.is_dir():
                    continue
                _reject_unsafe_path(info.filename)
                with zf.open(info) as member:
                    content = _read_bounded(member, budget)
                budget -= len(content)
                files[info.filename] = content
    except zipfile.BadZipFile as e:
        raise PackageImportError(f"Not a valid zip archive: {e}") from e
    return files


def _extract_tar(data: bytes) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    budget = _MAX_EXTRACTED_BYTES
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
                    content = _read_bounded(extracted, budget)
                    budget -= len(content)
                    files[member.name] = content
    except tarfile.TarError as e:
        raise PackageImportError(f"Not a valid tar/tgz archive: {e}") from e
    return files


def _extract_archive(data: bytes) -> dict[str, bytes]:
    """Pick the parser from the bytes, not a name: a URL ending in .zip can redirect to a
    tarball, and an upload's name is whatever the user's machine called the file."""
    if tarfile.is_tarfile(io.BytesIO(data)):
        return _extract_tar(data)
    if zipfile.is_zipfile(io.BytesIO(data)):
        return _extract_zip(data)
    raise PackageImportError("Not a valid zip or tar/tgz archive.")


def _is_ignored_resource(rel_path: str) -> bool:
    """Dotfiles/dot-directories and build/VCS artifact directories are never
    imported as bundled resources — mirrors `loader._should_ignore_resource`
    for the in-repo builtin-pack path, applied here to third-party
    archives."""
    if rel_path.endswith(".pyc"):
        return True
    parts = rel_path.split("/")
    if any(part.startswith(".") for part in parts):
        return True
    return any(part in _IGNORED_RESOURCE_DIR_NAMES for part in parts[:-1])


def _reject_unsafe_path(path: str) -> None:
    """Zip-slip guard: reject absolute paths and any `..` traversal segment
    before a single byte is written/kept in memory."""
    if path.startswith("/") or path.startswith("\\") or re.search(r"(^|/)\.\.(/|$)", path):
        raise PackageImportError(f"Archive contains an unsafe path: {path!r}")


def _skill_id_from_path(path: str) -> str:
    """Parent directory of a SKILL.md path; empty string for a root SKILL.md."""
    if path == "SKILL.md":
        return ""
    return path.rsplit("/", 2)[-2]


def _select_skill_md(paths: list[str], skill_filter: str | None) -> str:
    if not paths:
        raise PackageImportError(
            "No SKILL.md found in the archive. Skills must include a SKILL.md file "
            "at the root (see agentskills.io/specification)."
        )
    if skill_filter:
        matches = [p for p in paths if _skill_id_from_path(p) == skill_filter]
        if not matches:
            available = sorted(_skill_id_from_path(p) or "SKILL.md" for p in paths)
            raise PackageImportError(
                f"Skill {skill_filter!r} was not found in the package. "
                f"Available skills: {', '.join(available)}."
            )
        return matches[0]
    if "SKILL.md" in paths:
        return "SKILL.md"
    if len(paths) > 1:
        names = ", ".join(sorted(_skill_id_from_path(p) for p in paths))
        raise PackageImportError(
            f"This package contains multiple skills ({names}). "
            "Pass --skill <name> to choose one."
        )
    return paths[0]


def _files_to_preview(
    files: dict[str, bytes], *, source_label: str, skill_filter: str | None = None,
) -> ImportPreview:
    if not files:
        raise PackageImportError("Archive is empty.")

    prefix = _strip_common_prefix(list(files))
    stripped = {(p[len(prefix):] if prefix else p): content for p, content in files.items()}

    skill_md_paths = [p for p in stripped if p == "SKILL.md" or p.endswith("/SKILL.md")]
    skill_md_path = _select_skill_md(skill_md_paths, skill_filter)
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
        if _is_ignored_resource(rel):
            continue
        try:
            validator.validate_resource_path(rel)
        except SkillFormatError as e:
            logger.warning("Skipping resource with an invalid path in archive: %s", e)
            continue
        text = _decode_text(data)
        if text is None:
            skipped.append(rel)
            continue
        resources[rel] = text

    try:
        validator.validate_resource_budget(resources)
    except SkillFormatError as e:
        raise PackageImportError(str(e)) from e

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

    def __init__(self, fetcher: PublicUrlFetcher | None = None) -> None:
        self._fetcher = fetcher or PublicUrlFetcher()

    async def _get(self, url: str, limits: PublicFetchLimits, label: str) -> PublicFetchResponse:
        try:
            return await self._fetcher.get(url, limits)
        except UnsafeUrlError as e:
            logger.info("Blocked skill import download of %s: %s", redact_url(url), e)
            raise PackageImportError(_UNSAFE_URL_MESSAGE) from e
        except ResponseTooLargeError as e:
            raise PackageImportError(
                f"{label.capitalize()} is too large (limit {limits.max_bytes // _MIB} MB)."
            ) from e
        except PublicFetchError as e:
            logger.warning("Skill import download of %s failed: %s", redact_url(url), e)
            raise PackageImportError(f"Could not download {label}.") from e

    async def _download(
        self,
        url: str,
        limits: PublicFetchLimits,
        label: str,
        *,
        not_found_message: str | None = None,
    ) -> bytes:
        """Fetch ``url`` through the SSRF-safe fetcher. Every failure becomes a
        user-safe `PackageImportError`; internal details only reach the server log."""
        response = await self._get(url, limits, label)
        status = response.status_code
        if status == HTTPStatus.NOT_FOUND and not_found_message:
            raise PackageImportError(not_found_message)
        if not HTTPStatus.OK <= status < HTTPStatus.MULTIPLE_CHOICES:
            raise PackageImportError(f"Could not download {label} (HTTP {status}).")
        return response.content

    async def preview_npm(self, spec: PackageSpec) -> ImportPreview:
        raw_manifest = await self._download(
            f"{_NPM_REGISTRY_BASE}/{spec.name}/{spec.version}",
            _MANIFEST_LIMITS,
            "the npm package metadata",
            not_found_message=f"Package {spec.registry_spec!r} was not found on the npm registry.",
        )
        try:
            manifest = _NpmVersionManifest.model_validate_json(raw_manifest)
        except ValidationError as e:
            raise PackageImportError(f"The npm registry returned an invalid entry for {spec.name!r}.") from e
        tarball_url = manifest.dist.tarball
        if not tarball_url:
            raise PackageImportError(f"npm registry entry for {spec.name!r} has no downloadable tarball.")
        resolved_version = manifest.version or spec.version

        data = await self._download(tarball_url, _ARCHIVE_LIMITS, "the package tarball")
        files = _extract_tar(data)
        return _files_to_preview(
            files, source_label=f"npm:{spec.name}@{resolved_version}", skill_filter=spec.skill_filter,
        )

    @staticmethod
    def _codeload_url(owner: str, repo: str, ref: str = "HEAD") -> str:
        return f"https://codeload.github.com/{owner}/{repo}/tar.gz/{ref}"

    @staticmethod
    def _normalize_url(url: str) -> str:
        """Turn a GitHub repo page URL into a codeload tarball URL."""
        m = _GITHUB_REPO_RE.match(url)
        if m:
            return SkillPackageImporter._codeload_url(m["owner"], m["repo"], m.group("ref") or "HEAD")
        return url

    async def _github_contents_files(
        self, owner: str, repo: str, path: str, ref: str | None = None,
    ) -> dict[str, bytes] | None:
        """List a GitHub directory via the Contents API. Returns None on 403/404
        so callers can fall back to a tarball (unauthenticated Contents is
        rate-limited)."""
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}" if path else (
            f"https://api.github.com/repos/{owner}/{repo}/contents"
        )
        if ref and ref != "HEAD":
            url += f"?ref={ref}"
        response = await self._get(url, _MANIFEST_LIMITS, "the GitHub directory listing")
        if response.status_code in (HTTPStatus.FORBIDDEN, HTTPStatus.NOT_FOUND):
            return None
        if not HTTPStatus.OK <= response.status_code < HTTPStatus.MULTIPLE_CHOICES:
            raise PackageImportError(
                f"Could not download the GitHub directory listing (HTTP {response.status_code})."
            )
        try:
            payload = json.loads(response.content)
        except json.JSONDecodeError as e:
            raise PackageImportError("GitHub returned an invalid directory listing.") from e
        items: list[dict[str, Any]]
        if isinstance(payload, dict) and payload.get("type") == "file":
            items = [payload]
        elif isinstance(payload, list):
            items = [item for item in payload if isinstance(item, dict)]
        else:
            return None

        files: dict[str, bytes] = {}
        for item in items:
            item_path = item.get("path")
            if not isinstance(item_path, str):
                continue
            if item.get("type") == "file" and item.get("download_url"):
                files[item_path] = await self._download(
                    str(item["download_url"]), _ARCHIVE_LIMITS, "a skill file",
                )
            elif item.get("type") == "dir":
                nested = await self._github_contents_files(owner, repo, item_path, ref)
                if nested:
                    files.update(nested)
        return files or None

    async def preview_url(self, url: str, skill_filter: str | None = None) -> ImportPreview:
        if not url.lower().startswith(("https://", "http://")):
            raise PackageImportError("Only http(s) URLs are supported.")

        tree = _GITHUB_TREE_RE.match(url)
        if tree:
            files = await self._github_contents_files(
                tree["owner"], tree["repo"], tree["subpath"].rstrip("/"), tree["ref"],
            )
            if files:
                return _files_to_preview(files, source_label=f"url:{url}", skill_filter=skill_filter)

        repo = _GITHUB_REPO_RE.match(url)
        if repo and skill_filter:
            ref = repo.group("ref")
            for subpath in (f"skills/{skill_filter}", skill_filter):
                files = await self._github_contents_files(repo["owner"], repo["repo"], subpath, ref)
                if files:
                    return _files_to_preview(
                        files, source_label=f"url:{url}", skill_filter=skill_filter,
                    )

        download_url = self._normalize_url(url)
        data = await self._download(download_url, _ARCHIVE_LIMITS, "the archive")
        return _files_to_preview(
            _extract_archive(data), source_label=f"url:{url}", skill_filter=skill_filter,
        )

    async def preview_catalog_slug(self, slug: str, skill_filter: str | None = None) -> ImportPreview:
        raw = await self._download(
            _CATALOG_INSTALL_URL.format(slug=slug),
            _MANIFEST_LIMITS,
            "the catalog entry",
            not_found_message=f"Catalog skill {slug!r} was not found.",
        )
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as e:
            raise PackageImportError(f"Catalog returned an invalid entry for {slug!r}.") from e
        repository = None
        if isinstance(payload, dict):
            urls = payload.get("urls")
            if isinstance(urls, dict):
                repository = urls.get("repository")
        if not isinstance(repository, str) or not repository:
            raise PackageImportError(f"Catalog entry for {slug!r} has no repository URL.")
        gh = _GITHUB_OWNER_REPO_RE.match(repository)
        source = (
            f"https://github.com/{gh['owner']}/{gh['repo']}" if gh else repository
        )
        return await self.preview_url(source, skill_filter=skill_filter)

    def preview_upload(self, filename: str, data: bytes) -> ImportPreview:
        if len(data) > _MAX_ARCHIVE_BYTES:
            raise PackageImportError(f"Uploaded file is too large ({len(data)} bytes).")
        return _files_to_preview(_extract_archive(data), source_label=f"upload:{filename}")
