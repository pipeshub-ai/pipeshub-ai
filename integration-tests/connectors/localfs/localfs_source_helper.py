# pyright: ignore-file

"""Writes files for the Local FS connector to sync.

The other connector suites talk to a server; this one writes to a directory. The
directory is bind-mounted into the connector container by the integration
compose file, so the test writes on the host side and the connector reads the
same bytes on the container side.

Method names follow the protocol the shared lifecycle helpers expect
(``list_objects``, ``clear_objects``), so the same destructor works here.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Sequence


class LocalFsSourceHelper:
    """Creates and removes the files a connector test syncs from."""

    def __init__(self, host_dir: str) -> None:
        self.root = Path(host_dir)

    def ensure_root(self) -> None:
        """Create the directory and prove it is writable.

        A bind-mount target created by Docker is owned by root, which the test
        process cannot write to. Failing here with a clear reason beats failing
        later inside a sync with an empty result.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        probe = self.root / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()

    def list_objects(self, resource_name: str | None = None) -> List[str]:
        """Relative paths of every file under the root."""
        del resource_name  # the root is fixed at construction
        return sorted(
            str(p.relative_to(self.root))
            for p in self.root.rglob("*")
            if p.is_file() and not p.name.startswith(".")
        )

    def write_files(self, files: Sequence[tuple[str, str]]) -> int:
        """Write ``(relative_path, text)`` pairs, creating parent folders."""
        for rel_path, text in files:
            target = self.root / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        return len(files)

    def overwrite_file(self, rel_path: str, text: str) -> None:
        (self.root / rel_path).write_text(text, encoding="utf-8")

    def clear_objects(self, resource_name: str | None = None) -> None:
        """Remove every file the tests wrote, leaving the directory in place.

        The directory itself is the bind-mount target and must survive, so this
        deletes contents rather than the root.
        """
        del resource_name
        for path in sorted(self.root.rglob("*"), reverse=True):
            if path.name == ".gitkeep":
                continue
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
