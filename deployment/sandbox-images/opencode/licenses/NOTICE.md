# Third-party notices: PipesHub opencode engine image (`pxh-opencode:1.18.34`)

This image runs the unmodified opencode v1.18.34 release binary
(`opencode-linux-x64-musl.tar.gz`, release asset sha256
`9dd4470885d07205e31b702465564aaee97be26ee3dc192ec6702a15e7a21272`, binary sha256
`4fa437184846977f1849ffb65fc19d32686dec85a455df3d37f69a2993f22182`) from
<https://github.com/anomalyco/opencode/releases/tag/v1.18.34>.

| Component | Licence | Notes |
|---|---|---|
| opencode | MIT | `opencode-LICENSE` |
| Bun runtime (embedded in the opencode binary) | MIT | `bun-LICENSE.md` |
| JavaScriptCore / WebKit (statically linked by Bun) | LGPL-2.0 | See below |
| tinycc (statically linked by Bun) | LGPL-2.1 | See below |
| Other libraries statically linked by Bun | various permissive (see `bun-LICENSE.md`) | |
| `pxh-relay` (this image's `connect` relay) | PipesHub's licence | Built from `relay/pxh-relay.c` in this image's source |
| Alpine Linux base packages (busybox, musl, libstdc++, libgcc, git, bash, ripgrep) | their own licences, in `/usr/share/licenses` and the Alpine package database | Unmodified distribution packages, aggregated in the image |

## LGPL components inside the opencode binary

Bun statically links JavaScriptCore/WebKit (LGPL-2.0) and tinycc (LGPL-2.1). PipesHub does not
modify them. To exercise the LGPL right to modify the library and relink the application:

- the patched WebKit Bun uses: <https://github.com/oven-sh/WebKit>;
- Bun's source at the version embedded in opencode v1.18.34: <https://github.com/oven-sh/bun>;
- relinking steps, from `bun-LICENSE.md`: clone the WebKit fork into `vendor/WebKit`, run
  `bun sync-webkit-source`, then `bun run build:local`, which outputs a new `bun` binary; opencode's
  own build (<https://github.com/anomalyco/opencode>, tag `v1.18.34`) compiles its sources with that
  runtime.

The binary also carries tinycc's runtime source text (`libtcc1`, from GCC's runtime library), licensed
GPL-2.0-or-later with the GCC runtime exception, which permits linking it into any program. E5's
licence scanner reports it as an undeclared GPL marker; its classification is part of the legal review.

Whether shipping this image to customers is distribution under the LGPL, and the D29 decision on
weak-copyleft components, are for legal sign-off (PROJECT-PLAN D29). Running the image only inside
PipesHub-operated infrastructure is not distribution.
