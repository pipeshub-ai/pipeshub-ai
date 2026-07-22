from __future__ import annotations

import re

from app.agent_loop_lib.sandbox.coding.base import CodingLanguage

"""Shared npm/PyPI package-spec validation — extracted out of
`environment.py` (rather than left private to `EnvironmentManager`) so
every `CodingSandboxBackend` implementation (`LocalCodingSandbox`,
`E2BCodingSandbox`, future Daytona/AIO backends) validates package specs
against the exact same rules without reaching into another module's
private functions or re-implementing the regexes. This is the deeper,
syntactic defense layer — the `coding_sandbox_safety` PRE_TOOL_USE
middleware's URL/`git+`/`file:` denylist is a cheaper, earlier check on
the same class of input, not a replacement for this validation.
"""

__all__ = [
    "canonical_package_key",
    "matches_package_set",
    "package_name",
    "validate_package_spec",
]

# Deliberately restrictive character classes — anything outside these
# (git+, http://, file:, leading '-', whitespace, shell metacharacters)
# fails validation rather than being escaped/interpreted.
_NPM_SCOPED_RE = re.compile(r"^(@[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*)(@[a-zA-Z0-9._\-^~*]+)?$")
_NPM_UNSCOPED_RE = re.compile(r"^([a-z0-9][a-z0-9._-]*)(@[a-zA-Z0-9._\-^~*]+)?$")
_PYPI_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)([<>=!~]=?[A-Za-z0-9.*,]*)?$")


def _validate_npm_spec(spec: str) -> bool:
    if not spec or spec != spec.strip() or any(c.isspace() for c in spec):
        return False
    return bool(_NPM_SCOPED_RE.match(spec) or _NPM_UNSCOPED_RE.match(spec))


def _validate_pypi_spec(spec: str) -> bool:
    if not spec or any(c.isspace() for c in spec):
        return False
    return bool(_PYPI_RE.match(spec))


def validate_package_spec(spec: str, language: CodingLanguage) -> bool:
    """True iff `spec` is a safe npm ("pkg", "pkg@1.2.3", "@scope/pkg") or
    PyPI ("pkg", "pkg==1.2.3") package spec — no shell metacharacters, no
    `git+`/`file:`/URL specs, no leading `-flag` injection."""
    validator = _validate_npm_spec if language == "typescript" else _validate_pypi_spec
    return validator(spec)


def package_name(spec: str, language: CodingLanguage) -> str:
    """Extract the bare package name from a version-qualified spec (e.g.
    `"lodash@4.17.21"` -> `"lodash"`, `"numpy==1.26.0"` -> `"numpy"`) — used
    for allowlist/denylist checks and installed-package tracking. Returns
    `spec` unchanged if it doesn't match the expected shape."""
    if language == "typescript":
        match = _NPM_SCOPED_RE.match(spec) or _NPM_UNSCOPED_RE.match(spec)
    else:
        match = _PYPI_RE.match(spec)
    return match.group(1) if match else spec


def canonical_package_key(name: str, language: CodingLanguage) -> str:
    """Canonical allow/denylist key for a bare package name: lowercased,
    and for Python also `_` -> `-` (PEP 503 normalization, so `python_docx`
    and `python-docx` compare equal). npm names are left as-is beyond
    lowercasing — underscores are significant in npm names."""
    key = name.lower()
    if language != "typescript":
        key = key.replace("_", "-")
    return key


def matches_package_set(name: str, package_set: set[str], language: CodingLanguage) -> bool:
    """Membership check for allow/denylists that tolerates spelling
    variants the ecosystem itself treats as equal (`Pillow` vs `pillow`,
    `python_docx` vs `python-docx`) — pip/PyPI normalize these, so an
    exact string comparison would reject specs the installer accepts.
    Compares both the raw name and its canonical key against the set and
    the set's canonicalized entries."""
    if name in package_set:
        return True
    key = canonical_package_key(name, language)
    return any(canonical_package_key(entry, language) == key for entry in package_set)
