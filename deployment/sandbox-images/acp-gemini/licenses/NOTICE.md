# Third-party notices: PipesHub Gemini CLI (ACP) engine image (`pxh-gemini-tools:0.63.0-ph7`)

This image runs Google's unmodified Gemini CLI v0.63.0 release bundle (`gemini-cli-bundle.zip`, release asset
sha256 `c9d66e50a0fe098a32486360308e558c472809da276caf2aac2769d8f10571c2`, entry point `gemini.js` sha256
`5aee9ecb65b0b821e36a12f6e9e837301636de1a849a5a8bcc9c2b2620bbd72e`) from
<https://github.com/google-gemini/gemini-cli/releases/tag/v0.63.0>.

| Component | Licence | Notes |
|---|---|---|
| Gemini CLI | Apache-2.0 | `gemini-cli-LICENSE` |
| npm packages bundled into the release's chunks | their own licences | Listed by the project's `package-lock.json` at tag `v0.63.0`; not yet scanned by `scripts/engines/licence_scan.py` (open before the profile is signed for release) |
| Node.js (`nodejs`), ripgrep, git, bash, busybox, musl | their own licences, in `/usr/share/licenses` and the Alpine package database | Unmodified distribution packages, aggregated in the image |
| `pxh-relay` (this image's `connect` relay) | PipesHub's licence | Built from `relay/pxh-relay.c` in this image's source |
| CPython 3.12 and the wheels in `tools/requirements.lock`, the npm packages in `tools/node/package-lock.json` | see `/usr/share/licenses/pxh-gemini-tools/` | The same runtime layer as the opencode tools image (E0b, SKILLPACKS) |
