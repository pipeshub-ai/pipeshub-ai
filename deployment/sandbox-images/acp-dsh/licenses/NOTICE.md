# DeepSeek Harness (acp-dsh) image notices

DeepSeek Harness (`@deepseek-ai/dsh` 0.2.2-rc.1) is MIT-licensed (`dsh-LICENSE`, Copyright (c) 2026 DeepSeek). The
image installs it with the exact dependency tree of `engine/package-lock.json`; each package keeps its own licence file
under `/usr/local/lib/dsh/node_modules`. The tree's licences, read from every installed package's `package.json` on
2026-11-07 (WP-105): MIT 562, Apache-2.0 55, BSD-3-Clause 16, ISC 11, BSD-2-Clause 2, 0BSD, BlueOak-1.0.0, Python-2.0
(argparse), Unlicense (fast-sha256), MIT OR CC0-1.0 (type-fest), and the weak-copyleft components below (D29: they
pass with this notice and a source pointer).

| Component | Version | Licence | Source | How it is used |
|---|---|---|---|---|
| `@deepseek-ai/libreoffice-kit` | 0.1.5 | MPL-2.0 | https://www.npmjs.com/package/@deepseek-ai/libreoffice-kit (LibreOffice: https://git.libreoffice.org/core) | unmodified npm files, loaded by dsh's office-to-PDF plugin (not mounted by the ACP profile) |
| `@deepseek-ai/libreoffice-kit-wasm` | 0.1.5 | MPL-2.0 | https://www.npmjs.com/package/@deepseek-ai/libreoffice-kit-wasm | unmodified WebAssembly build, as above |
| `@img/sharp-libvips-linux-x64` | 1.3.4 | LGPL-3.0-or-later | https://github.com/lovell/sharp-libvips (libvips: https://github.com/libvips/libvips) | an unmodified shared library loaded at run time by sharp; replaceable in place |

Node.js (`/usr/share/licenses/nodejs/LICENSE`) is the official build of the release the Dockerfile names.

## PipesHub's layers (the run image, `scan.sh`)

The run image, `pxh-dsh-tools:<version>`, is this image built on CPython 3.12.15 for Debian bookworm
(`python:3.12.15-slim-bookworm`, Python Software Foundation License Version 2,
`/usr/local/lib/python3.12/LICENSE.txt`), with the Python and npm packages of `pxh-opencode-tools` at the same
versions (their notices and `components.json` in `/usr/share/licenses/pxh-dsh-tools/`, shared with the other tools
images), the relay (`pxh-relay`, built from `deployment/sandbox-images/opencode/relay/pxh-relay.c`) and the Debian
bookworm packages its apt layer and the CPython base add (`git`, `ripgrep` and their dependencies), each unmodified
and aggregated under its own licence in `/usr/share/doc/*/copyright`.

`scan.sh` (`debian-layer-scan.sh acp-dsh`) scans those layers, never dsh's tree. On `pxh-dsh-tools:0.2.2-rc.1-ph7`
(2026-11-11): 333 packages, 0 marker failures, 32 notices (the shared tools layer's dated approvals), verdict `fail`
on 23 Debian packages no recorded approval covers, the same 23 Claude Code's image carries: `git`, `git-man`,
`netbase` (GPL-2.0-only); `ca-certificates`, `libkeyutils1` (GPL-2.0-or-later); `libgdbm6`, `libgdbm-compat4`,
`libreadline8`, `readline-common` (GPL-3.0-or-later); `perl`, `perl-modules-5.36`, `libperl5.36`, `liberror-perl`
(GPL-1.0-or-later OR Artistic-1.0-Perl); `libsasl2-2`, `libsasl2-modules-db` (BSD-4-Clause); `libldap-2.5-0`
(OLDAP-2.8); `libcurl3-gnutls` (curl); `libsqlite3-0` (public domain); `libgssapi-krb5-2`, `libk5crypto3`,
`libkrb5-3`, `libkrb5support0`, `librtmp1` (no machine-readable licence). They are unmodified distribution packages,
the git and perl the agent's shell runs as separate programs; approving them is the legal review of D29, with the
release signing (OB-1), not code.
