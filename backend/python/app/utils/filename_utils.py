import os
import re
from pathlib import Path


def sanitize_filename_for_content_disposition(
    filename: str,
    fallback: str = "file"
) -> str:
    """
    Sanitize a filename for use in HTTP Content-Disposition headers.

    The Content-Disposition header requires latin-1 encoding per RFC 2616.
    This function converts filenames to latin-1, ignoring non-compatible characters.

    Args:
        filename: The original filename to sanitize
        fallback: Fallback filename if sanitization results in empty string

    Returns:
        A latin-1 compatible filename safe for Content-Disposition headers
    """
    # Replace newlines, carriage returns, tabs, etc. with a space (or remove them)
    filename = re.sub(r'[\r\n\t\x00-\x1f\x7f]', ' ', filename)
    # Collapse multiple spaces into one and strip leading/trailing whitespace
    filename = re.sub(r' +', ' ', filename).strip()
    return filename.encode('latin-1', 'ignore').decode('latin-1') or fallback


_PATH_SEPARATOR_CHARS = ("/", "\\", "\x00")


def upload_extension(filename: str | None, allowed: frozenset[str]) -> str | None:
    """Lower-case extension (no dot) of a client-supplied upload name, or None when the
    name is empty, contains a path separator or NUL, has no stem, or its extension is
    not in ``allowed``. Callers must never use the name itself as a path; this only
    tells them how to handle the bytes.
    """
    if not filename or any(ch in filename for ch in _PATH_SEPARATOR_CHARS):
        return None
    stem, dot, ext = filename.rpartition(".")
    if not dot or not stem.strip(". "):
        return None
    ext = ext.lower()
    return ext if ext in allowed else None


def temp_path_for(directory: str, name: str | None, fallback: str = "file") -> str:
    """Where to write a file called ``name`` inside ``directory``. Only the last component
    of the name is kept, so a name that carries a path cannot land outside the directory.
    """
    base = Path(name).name if name else ""
    return os.path.join(directory, base if base not in ("", "..") else fallback)
