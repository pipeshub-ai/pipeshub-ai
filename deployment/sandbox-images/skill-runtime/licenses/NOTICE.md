# Third-party notices: SK1 skill-runtime test images (`pxh-sk1-opencode:test`, `pxh-sk1-codex:test`)

Test images only, never shipped. They are the opencode tools image (`pxh-opencode-tools:1.18.34`, notices in
`/usr/share/licenses/pxh-opencode/` and `/usr/share/licenses/pxh-opencode-tools/`) with the Python packages below
installed unmodified from their published wheels; the Codex variant also carries the unmodified Codex app-server
binary of `pxh-codex:0.160.0` (notices in `/usr/share/licenses/pxh-codex/`). Each wheel's own licence files stay in
`/usr/local/lib/python3.12/site-packages/<package>.dist-info/`.

| Package | Version | Licence |
|---|---|---|
| defusedxml | 0.7.1 | PSF-2.0 |

pypdf, reportlab and charset-normalizer, which these images used to add, are in the tools image itself since
SKILLPACKS; their notices are in `/usr/share/licenses/pxh-opencode-tools/`.

The skills themselves (Anthropic's `pdf` and `pptx`) are not part of these images: tests place them in a sandbox at
run time from a local install. Their licence is proprietary (Anthropic's), so they are never copied into an image.
