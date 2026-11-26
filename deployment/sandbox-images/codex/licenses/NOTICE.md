# Third-party notices: PipesHub Codex engine image (`pxh-codex:0.160.0`)

This image runs the unmodified standalone Codex app-server binary of release `rust-v0.160.0`
(`codex-app-server-x86_64-unknown-linux-musl.tar.gz`, release asset sha256
`d0a05909836d4a73634641b89c7a62fd99c0978cdfe7ae9b8b631e9ee6d9da46`, binary sha256
`d9244ef26a5193aec701e61c2f9280d44040efbedb9c16b4c0c6f41e354bcdd3`) from
<https://github.com/openai/codex/releases/tag/rust-v0.160.0>. Only that binary is shipped: not the
`-package-` download, which adds bubblewrap (LGPL), zsh and ripgrep builds of its own.

| Component | Licence | Notes |
|---|---|---|
| Codex (`codex-app-server`) | Apache-2.0 | `codex-LICENSE`, `codex-NOTICE` (includes code derived from Ratatui, MIT) |
| Rust crates compiled into the binary (940 in the build closure) | permissive (MIT, Apache-2.0, BSD, ISC, Unicode, Zlib, …) except the 12 below | E5's scanner, `scripts/engines/licence_scan.py codex 0.160.0`, verdict `pass_with_notices` |
| 12 crates listed below | MPL-2.0 | Unmodified; source pointers below |
| `pxh-relay` (this image's `connect` relay) | PipesHub's licence | Built from `deployment/sandbox-images/opencode/relay/pxh-relay.c` |
| Alpine Linux base packages (busybox, musl, bash, git, ripgrep and their libraries) | their own licences, in `/usr/share/licenses` and the Alpine package database | Unmodified distribution packages, aggregated in the image |

The run image, `pxh-codex-tools:0.160.0`, is this image built on CPython 3.12 for Alpine with the same
unmodified Python packages, Node.js and npm packages as `pxh-opencode-tools:1.18.34`; their notices and `components.json` are in
`/usr/share/licenses/pxh-codex-tools/`.

## MPL-2.0 code inside the Codex binary

Shipped unmodified; MPL-2.0 is file-level copyleft and does not extend to other code. Source of each
component, at the version compiled in:

| Component | Source |
|---|---|
| nucleo-matcher 0.3.1 | <https://crates.io/crates/nucleo-matcher/0.3.1> |
| nucleo 0.5.0 | <https://crates.io/crates/nucleo/0.5.0> |
| option-ext 0.2.0 | <https://crates.io/crates/option-ext/0.2.0> |
| symphonia-bundle-mp3 0.6.0 | <https://crates.io/crates/symphonia-bundle-mp3/0.6.0> |
| symphonia-common 0.6.0 | <https://crates.io/crates/symphonia-common/0.6.0> |
| symphonia-core 0.6.0 | <https://crates.io/crates/symphonia-core/0.6.0> |
| symphonia-format-isomp4 0.6.0 | <https://crates.io/crates/symphonia-format-isomp4/0.6.0> |
| symphonia-format-mkv 0.6.0 | <https://crates.io/crates/symphonia-format-mkv/0.6.0> |
| symphonia-format-ogg 0.6.0 | <https://crates.io/crates/symphonia-format-ogg/0.6.0> |
| symphonia-format-riff 0.6.0 | <https://crates.io/crates/symphonia-format-riff/0.6.0> |
| symphonia-metadata 0.6.0 | <https://crates.io/crates/symphonia-metadata/0.6.0> |
| symphonia 0.6.0 | <https://crates.io/crates/symphonia/0.6.0> |

Accepting MPL-2.0 inside engines is the D29 decision (legal sign-off, PROJECT-PLAN D29). Running the
image only inside PipesHub-operated infrastructure is not distribution.
