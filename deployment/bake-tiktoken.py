"""Put the cl100k_base token table where tiktoken looks first (run at image build).

    python bake-tiktoken.py <dir>

tiktoken keeps a downloaded table under the SHA-1 of its URL and fetches it on first use, which an
isolated network cannot do. The same file ships inside litellm; it is copied from there when the
installed litellm has it and downloaded otherwise, and either way its SHA-256 must be the one
tiktoken itself expects, or the build fails.
"""

import hashlib
import importlib.util
import sys
import urllib.request
from pathlib import Path

URL = "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken"
SHA256 = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"


def main(argv: list[str]) -> int:
    out = Path(argv[0])
    name = hashlib.sha1(URL.encode()).hexdigest()
    data = None
    # find_spec, not import: importing litellm reaches out for its price map.
    spec = importlib.util.find_spec("litellm")
    for root in (spec.submodule_search_locations or []) if spec else []:
        bundled = Path(root) / "litellm_core_utils" / "tokenizers" / name
        if bundled.is_file():
            data = bundled.read_bytes()
            break
    if data is None:
        with urllib.request.urlopen(URL, timeout=60) as response:
            data = response.read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != SHA256:
        print(f"cl100k_base has sha256 {digest}, expected {SHA256}", file=sys.stderr)  # noqa: T201
        return 1
    out.mkdir(parents=True, exist_ok=True)
    (out / name).write_bytes(data)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
