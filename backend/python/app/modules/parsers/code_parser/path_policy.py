"""Which repository paths get records: one policy for every git connector.

Combines the built-in rules (``file_role``: dependency, build-output, cache,
generated and credential paths), the repo's own ``.gitattributes`` linguist
marks, and the paths a connector's admin excluded. GitHub and GitLab both ask
this, so a path gets the same answer from either (P-1, P-7, S-2).
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

from app.modules.parsers.code_parser.file_role import (
    is_sensitive_path,
    should_index_code_file,
    should_index_code_folder,
)
from app.modules.parsers.code_parser.gitattributes import LinguistExclusions, glob_regex

__all__ = ["PathPolicy"]


@dataclass(frozen=True)
class PathPolicy:
    linguist: LinguistExclusions = LinguistExclusions()
    # gitignore-style globs from the connector's "Exclude paths" filter. They
    # only ever exclude more: credential files stay excluded whatever is set.
    exclude_globs: tuple[str, ...] = ()

    def keeps_file(self, path: str, name: str | None = None) -> bool:
        if is_sensitive_path(name or path) or self._excluded(path):
            return False
        if self.linguist.forces_in(path):
            return True
        return should_index_code_file(path, name)[0] and not self.linguist.excludes(path)

    def keeps_folder(self, path: str) -> bool:
        if self._excluded(path):
            return False
        return should_index_code_folder(path) or self.linguist.may_force_in_under(path)

    def _excluded(self, path: str) -> bool:
        """The path, or any folder above it, matches an exclude glob (`docs/`, `**/fixtures/**`)."""
        if not self._regexes:
            return False
        parts = path.strip("/").split("/")
        candidates = [path, f"{path.rstrip('/')}/",
                      *("/".join(parts[:i]) + "/" for i in range(1, len(parts)))]
        return any(regex.match(c) for regex in self._regexes for c in candidates)

    @cached_property
    def _regexes(self) -> tuple:
        return tuple(glob_regex(g.strip()) for g in self.exclude_globs if g and g.strip())
