# Third-party notices: PipesHub Goose (ACP) engine image (`pxh-goose-tools:1.57.0-ph7`)

This image runs the AAIF's unmodified Goose v1.57.0 release binary (`goose-x86_64-unknown-linux-musl.tar.gz`, release
asset sha256 `6c3a05e30e1a425dc06a410dbe24b3f3697c9721b1ea2340434ddda6b64267ae`, the GitHub release's own digest; binary
`goose` sha256 `bb259a6f0dd02ce9c5e3793edb91e68a84d40eb24478e8b28dd556ebf3681dc1`) from
<https://github.com/aaif-goose/goose/releases/tag/v1.57.0>.

| Component | Licence | Notes |
|---|---|---|
| Goose | Apache-2.0 | `goose-LICENSE` (the repository's `LICENSE` at tag `v1.57.0`) |
| Rust crates statically linked into the binary (1,112 in `Cargo.lock` at tag `v1.57.0`) | permissive (MIT, Apache-2.0, BSD, ISC, Unicode, Zlib, bzip2-1.0.6, …) except the 24 below | E5's scanner, `scripts/engines/licence_scan.py acp-goose 1.57.0`, 2026-11-03: `pass_with_notices` with the owner's recorded approval (no cargo on the scanning host, so the whole `Cargo.lock` was judged: a superset of the build) |
| ansi_colours 1.2.3 | LGPL-3.0-or-later | Statically linked; see below. `LGPL-3.0.txt`, `GPL-3.0.txt` |
| 23 crates listed below | MPL-2.0 | Unmodified; source pointers below |
| Node.js (`nodejs`), ripgrep, git, bash, busybox, musl | their own licences, in `/usr/share/licenses` and the Alpine package database | Unmodified distribution packages, aggregated in the image |
| `pxh-relay` (this image's `connect` relay) | PipesHub's licence | Built from `relay/pxh-relay.c` in this image's source |
| CPython 3.12 and the wheels in `tools/requirements.lock`, the npm packages in `tools/node/package-lock.json` | see `/usr/share/licenses/pxh-goose-tools/` | The same runtime layer as the opencode tools image (E0b, SKILLPACKS) |

## LGPL-3.0 code inside the Goose binary: ansi_colours 1.2.3

The Goose binary is a static Rust build, and it contains the library **ansi_colours 1.2.3**
(true-colour to ANSI palette conversion), unmodified, which is covered by the GNU Lesser General Public
License, version 3 or (at your option) any later version:

> ansi_colours – true-colour ↔ ANSI terminal palette converter
> Copyright 2018 by Michał Nazarewicz <mina86@mina86.com>
>
> ansi_colours is free software: you can redistribute it and/or modify it under the terms of the GNU Lesser
> General Public License as published by the Free Software Foundation; either version 3 of the License, or
> (at your option) any later version.
>
> ansi_colours is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the
> implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU Lesser General Public
> License for more details.

The licence texts are in this folder: `LGPL-3.0.txt` (the GNU LGPL version 3, as the crate ships it) and
`GPL-3.0.txt` (the GNU GPL version 3, which the LGPL incorporates).

To modify the library and relink Goose with it (LGPL-3.0 section 4(d)(0)), everything needed is public:

- the library's source, at the version compiled in: <https://crates.io/crates/ansi_colours/1.2.3>
  (crate archive sha256 `14eec43e0298190790f41679fe69ef7a829d2a2ddd78c8c00339e84710e435fe`),
  repository <https://github.com/mina86/ansi_colours>;
- the Corresponding Application Code, Goose's complete source under Apache-2.0 at the release tag:
  <https://github.com/aaif-goose/goose/tree/v1.57.0>, whose `Cargo.lock` pins ansi_colours 1.2.3 and every
  other crate of the build;
- to relink: change `Cargo.lock` (or add a `[patch.crates-io]` entry) to point at the modified ansi_colours,
  then `cargo build --release --target x86_64-unknown-linux-musl -p goose-cli`, and put the resulting `goose`
  in place of `/usr/local/bin/goose`.

**Written offer.** For three years from the date PipesHub last distributed this image, PipesHub will give
anyone who asks, at no more than the cost of physically performing the transfer, a copy of the source code
named above (ansi_colours 1.2.3 and Goose v1.57.0 with its `Cargo.lock`). Write to contact@pipeshub.com,
naming this image (`pxh-goose-tools:1.57.0`).

## MPL-2.0 code inside the Goose binary

Shipped unmodified; MPL-2.0 is file-level copyleft and does not extend to other code. Source of each
component, at the version compiled in: `https://crates.io/crates/<name>/<version>` for

colored 3.1.1, option-ext 0.2.0, symphonia 0.6.1, symphonia-bundle-mp3 0.6.1, symphonia-codec-aac 0.6.1,
symphonia-codec-adpcm 0.6.1, symphonia-codec-alac 0.6.1, symphonia-codec-pcm 0.6.1, symphonia-codec-vorbis 0.6.1,
symphonia-common 0.6.1, symphonia-core 0.6.1, symphonia-format-isomp4 0.6.1, symphonia-format-mkv 0.6.1,
symphonia-format-riff 0.6.1, symphonia-metadata 0.6.1, uniffi 0.32.1, uniffi_bindgen 0.32.1, uniffi_core 0.32.1,
uniffi_internal_macros 0.32.1, uniffi_macros 0.32.1, uniffi_meta 0.32.1, uniffi_pipeline 0.32.1, uniffi_udl 0.32.1.

## bzip2-1.0.6 code inside the Goose binary

libbz2-rs-sys 0.2.5 (<https://crates.io/crates/libbz2-rs-sys/0.2.5>, the Rust port of libbzip2) is under the
bzip2-1.0.6 licence, a BSD-style permissive licence: it asks that source redistributions keep its notice,
that altered versions be marked, and that its authors' names not endorse products. Shipped unmodified.
