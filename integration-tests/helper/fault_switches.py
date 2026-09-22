"""Shell snippets that take an outside dependency away inside the app container, and put it back.

The resilience tests run them with ``docker compose exec`` in the pipeshub-ai
container. Each "off" snippet keeps what it needs to undo itself, and each "on"
snippet is safe to run when nothing was switched off, so a test can always
call it in ``finally``.

* AI provider: the provider's host names are pointed at an address nothing
  listens on in ``/etc/hosts``, so every new connection is refused at once.
  ``/etc/hosts`` is a bind mount in a container: it is rewritten in place with
  ``cat >``, never replaced with ``mv``.
* Blob storage: the local storage folder is moved aside and a plain file takes
  its name, so writing into it fails for every user, root included. (A real
  full disk needs a mount the container is not allowed to make.)
"""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Mapping, Sequence
from urllib.parse import urlparse

# Where each provider the integration suite can configure sends its requests.
_PROVIDER_HOSTS = (
    "api.openai.com",
    "generativelanguage.googleapis.com",
    "api.groq.com",
)
_ENDPOINT_VARS = ("TEST_AZURE_OPENAI_ENDPOINT",)
_HOST_NAME = re.compile(r"^[A-Za-z0-9.-]+$")

# Matches the indexing service's own command line, not this shell's: the
# bracket keeps the pattern from matching the text of the pattern.
KILL_INDEXING = (
    "for p in /proc/[0-9]*; do "
    "if tr '\\0' ' ' < $p/cmdline 2>/dev/null | grep -q '^python[0-9.]* -m [a]pp[.]indexing_main'; "
    "then kill -9 ${p#/proc/} && echo ${p#/proc/}; fi; done"
)

HOSTS_FILE = "/etc/hosts"
HOSTS_BACKUP = "/tmp/pipeshub-resilience-hosts.bak"

LOCAL_STORAGE_ROOT = os.getenv("PIPESHUB_LOCAL_STORAGE_ROOT", "/root/.local")
LOCAL_STORAGE_MOUNT = os.getenv("PIPESHUB_LOCAL_STORAGE_MOUNT", "PipesHub")


def ai_provider_hosts(env: Mapping[str, str] | None = None) -> list[str]:
    """Every host the configured AI models may call: the public providers plus any configured endpoint."""
    env = os.environ if env is None else env
    hosts = list(_PROVIDER_HOSTS)
    for name in _ENDPOINT_VARS:
        endpoint = (env.get(name) or "").strip()
        if not endpoint:
            continue
        host = urlparse(endpoint if "://" in endpoint else f"https://{endpoint}").hostname
        if host and host not in hosts:
            hosts.append(host)
    return hosts


def block_hosts_script(hosts: Sequence[str], *, hosts_file: str = HOSTS_FILE, backup: str = HOSTS_BACKUP) -> str:
    """Point ``hosts`` at a refused address, keeping the original file to restore."""
    if not hosts:
        raise ValueError("no hosts to block")
    for host in hosts:
        if not _HOST_NAME.match(host):
            raise ValueError(f"not a host name: {host!r}")
    lines = "".join(f"127.0.0.1 {h}\\n::1 {h}\\n" for h in hosts)
    saved, target, staged = shlex.quote(backup), shlex.quote(hosts_file), shlex.quote(f"{backup}.new")
    return (
        f"{{ [ -f {saved} ] || cp {target} {saved}; }} && "
        f"{{ cat {saved}; printf '{lines}'; }} > {staged} && "
        f"cat {staged} > {target} && rm -f {staged}"
    )


def restore_hosts_script(*, hosts_file: str = HOSTS_FILE, backup: str = HOSTS_BACKUP) -> str:
    """Put ``/etc/hosts`` back as it was; does nothing if it was never changed."""
    saved = shlex.quote(backup)
    return f"if [ -f {saved} ]; then cat {saved} > {shlex.quote(hosts_file)} && rm -f {saved}; fi"


def _storage_paths(root: str, mount: str) -> tuple[str, str]:
    live = f"{root.rstrip('/')}/{mount}"
    return live, f"{live}.resilience-off"


def storage_off_script(root: str = LOCAL_STORAGE_ROOT, mount: str = LOCAL_STORAGE_MOUNT) -> str:
    """Make local blob storage unwritable. Fails, changing nothing, unless the folder is there."""
    live, aside = (shlex.quote(p) for p in _storage_paths(root, mount))
    return f"[ -d {live} ] && [ ! -e {aside} ] && mv {live} {aside} && : > {live}"


def storage_on_script(root: str = LOCAL_STORAGE_ROOT, mount: str = LOCAL_STORAGE_MOUNT) -> str:
    """Undo :func:`storage_off_script`; does nothing if storage was never switched off."""
    live, aside = (shlex.quote(p) for p in _storage_paths(root, mount))
    return f"if [ -d {aside} ]; then rm -f {live} && mv {aside} {live}; fi"
