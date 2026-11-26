"""Run: python3 -m unittest discover -s deployment/helm/tests -p 'test_*.py'

kind_smoke.sh installs the published image, the last release's, which cannot run on the hardened profile's read-only
root (its process monitor wrote a log under /app, and the app crash-looped for the whole install). The smoke installs
the chart's default profile unless SMOKE_HARDENED=1 asks for the hardened one. The script runs against stub tools that
record the helm install's arguments; nothing is installed.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "kind_smoke.sh"

STUB = """#!/usr/bin/env bash
if [ "$(basename "$0")" = helm ] && [ "$1" = install ]; then printf '%s\\n' "$@" > "$RECORD"; exit 1; fi
if [ "$(basename "$0")" = docker ] && [ "$1" = image ]; then [ -n "$NO_LOCAL" ] && exit 1; printf '%s' "$LABEL"; exit 0; fi
if [ "$(basename "$0")" = docker ] && [ "$1" = buildx ]; then [ -n "$REGISTRY" ] || exit 1; printf '%s' "$REGISTRY"; exit 0; fi
[ "$(basename "$0")" = openssl ] && echo 00
exit 0
"""


class KindSmokeProfileTest(unittest.TestCase):
    def _helm_install_args(self, env: dict[str, str]) -> tuple[int, list[str], str]:
        with tempfile.TemporaryDirectory() as tmp:
            stubs = Path(tmp) / "bin"
            stubs.mkdir()
            for tool in ("kind", "kubectl", "helm", "docker", "curl", "openssl"):
                stub = stubs / tool
                stub.write_text(STUB)
                stub.chmod(0o755)
            record = Path(tmp) / "helm-install.txt"
            done = subprocess.run(
                ["bash", str(SCRIPT)], capture_output=True, text=True, check=False, timeout=60,
                env={"PATH": f"{stubs}:/usr/bin:/bin", "RECORD": str(record), "TMPDIR": tmp, **env},
            )
            args = record.read_text().splitlines() if record.exists() else []
            return done.returncode, args, done.stderr

    def test_the_published_image_gets_the_default_profile(self) -> None:
        code, args, stderr = self._helm_install_args({})
        self.assertNotEqual(code, 0)  # the stub helm fails the install; only its arguments matter
        self.assertIn("hardened=false", args, stderr)
        self.assertNotIn("hardened=true", args)

    def test_smoke_hardened_installs_the_hardened_profile(self) -> None:
        _, args, stderr = self._helm_install_args({"SMOKE_HARDENED": "1"})
        self.assertIn("hardened=true", args, stderr)

    def test_the_image_label_decides_what_the_chart_renders(self) -> None:
        _, args, stderr = self._helm_install_args({"LABEL": "sandbox-broker,secret-key-file,agent-harness"})
        self.assertIn("image.capabilities=sandbox-broker\\,secret-key-file\\,agent-harness", args, stderr)

    def test_an_image_without_the_label_is_an_older_one(self) -> None:
        _, args, stderr = self._helm_install_args({"LABEL": ""})
        self.assertIn("image.capabilities=none", args, stderr)

    def test_the_label_is_read_from_the_registry_when_the_image_is_not_local(self) -> None:
        config = '{"linux/amd64":{"config":{"Labels":{"ai.pipeshub.capabilities": "secret-key-file"}}}}'
        _, args, stderr = self._helm_install_args({"NO_LOCAL": "1", "REGISTRY": config})
        self.assertIn("image.capabilities=secret-key-file", args, stderr)
        _, args, stderr = self._helm_install_args({"NO_LOCAL": "1", "REGISTRY": '{"config":{"Labels":{}}}'})
        self.assertIn("image.capabilities=none", args, stderr)

    def test_an_unreadable_label_leaves_the_chart_default(self) -> None:
        _, args, stderr = self._helm_install_args({"NO_LOCAL": "1"})
        self.assertFalse([a for a in args if a.startswith("image.capabilities=")], stderr)

    def test_any_other_value_is_refused_before_a_cluster_is_made(self) -> None:
        code, args, stderr = self._helm_install_args({"SMOKE_HARDENED": "yes"})
        self.assertNotEqual(code, 0)
        self.assertEqual(args, [])
        self.assertIn("SMOKE_HARDENED must be 0 or 1", stderr)


if __name__ == "__main__":
    unittest.main()
