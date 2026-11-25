"""The component manifest of PipesHub's layers of a Debian engine image (debian-layer-scan.sh, E9 IM-3cc, CCPY, C2.43).

    debian_manifest.py IMAGE_PKGS LICENCES OUT TOOLS_COMPONENTS NODE_VERSION

IMAGE_PKGS is `dpkg-query` output (`package<TAB>version`) of the image, LICENCES the first `License:` line of each
added package's copyright file (`package<TAB>licence`), TOOLS_COMPONENTS the Alpine tools layer's components.json.
OUT gets the manifest licence_scan.py reads with --manifest.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# Debian's copyright files name licences by the DEP-5 short names; the policy lists SPDX ids. Only names with one
# SPDX meaning are translated (`or` keeps its alternatives); anything else stays as Debian wrote it.
DEP5 = {
    "expat": "MIT", "bsd3": "BSD-3-Clause", "bsd-3-clause": "BSD-3-Clause", "bsd-2-clause": "BSD-2-Clause",
    "bsd-4-clause": "BSD-4-Clause", "gpl-1+": "GPL-1.0-or-later", "gpl-2": "GPL-2.0-only", "gpl-2+": "GPL-2.0-or-later",
    "gpl-3": "GPL-3.0-only", "gpl-3+": "GPL-3.0-or-later", "lgpl-2.1": "LGPL-2.1-only", "lgpl-2.1+": "LGPL-2.1-or-later",
    "artistic": "Artistic-1.0-Perl", "public-domain": "Public-Domain", "curl": "curl", "openldap-2.8": "OLDAP-2.8",
}


def spdx(licence: str) -> str:
    words = [w for w in re.split(r"\s+(or)\s+", licence.strip(), flags=re.I) if w]
    return " ".join("OR" if w.lower() == "or" else DEP5.get(w.lower(), w) for w in words) or "UNKNOWN"


def manifest(image_pkgs: str, licences: str, tools_components: list[dict[str, object]], node: str) -> list[dict[str, object]]:
    versions = dict(line.split("\t", 1) for line in image_pkgs.splitlines() if "\t" in line)
    components: list[dict[str, object]] = [
        {
            "name": name,
            "version": versions[name],
            "licence": spdx(licence),
            "ecosystem": "native",
            "source": f"https://sources.debian.org/src/{name}/{versions[name]}/",
        }
        for name, licence in (line.split("\t", 1) for line in licences.splitlines() if line.strip())
    ]
    components.append({"name": "pxh-relay", "version": "1", "licence": "Apache-2.0", "ecosystem": "native",
                       "source": "deployment/sandbox-images/opencode/relay/pxh-relay.c"})
    # CCPY: the tools layer's CPython, wheels, their bundled libraries and the npm packages, as the Alpine tools
    # images declare them (this image's Debian packages are the dpkg rows above).
    components += [c for c in tools_components if c["ecosystem"] != "apk"]
    # SKILLPACKS: the official glibc Node binary this image carries in place of Alpine's nodejs package.
    components.append({"name": "nodejs", "version": node, "licence": "MIT", "ecosystem": "runtime",
                       "source": f"https://github.com/nodejs/node/blob/v{node}/LICENSE"})
    return components


def main(argv: list[str]) -> int:
    image_pkgs, licences, out, tools, node = argv
    components = manifest(
        Path(image_pkgs).read_text(), Path(licences).read_text(), json.loads(Path(tools).read_text())["components"], node
    )
    Path(out).write_text(json.dumps({"components": components}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
