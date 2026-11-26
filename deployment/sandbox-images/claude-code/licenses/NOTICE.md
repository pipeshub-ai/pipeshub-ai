# Third-party notices: Claude Code engine image (`pxh-claude-code:<version>`)

This image is built by the customer's administrator, on the customer's infrastructure, from a copy of the
Claude Code binary the administrator obtained from Anthropic. **PipesHub does not distribute Claude Code**:
not in its repository, its images or its packages.

| Component | Licence | Supplied by |
|---|---|---|
| Claude Code (`/usr/local/bin/claude`) | Proprietary: "© Anthropic PBC. All rights reserved. Use is subject to Anthropic's Commercial Terms of Service" | The customer's administrator. Copied unmodified; accepted only when its sha256 is the tested version's digest in PipesHub's allowlist (`backend/python/harness_engines/allowlist.json`). Its use is governed by the customer's own agreement with Anthropic |
| `pxh-relay` (the image's `connect` relay) | PipesHub's licence | Built from `deployment/sandbox-images/opencode/relay/pxh-relay.c` |
| Debian bookworm base and packages (`git`, `ripgrep` and their dependencies) | their own licences, in `/usr/share/doc/*/copyright` | Unmodified distribution packages, aggregated in the image |

The run image, `pxh-claude-code-tools:<version>`, is this image built on CPython 3.12.15 for Debian bookworm
(`python:3.12.15-slim-bookworm`, Python Software Foundation License Version 2,
`/usr/local/lib/python3.12/LICENSE.txt`), with the Python packages of `pxh-opencode-tools` at the same versions,
installed unmodified from their published manylinux wheels, and Node.js (the official glibc build of the release
the Alpine tools images carry, `/usr/share/licenses/nodejs/LICENSE`) with the same npm packages. Their notices and `components.json` are in
`/usr/share/licenses/pxh-claude-code-tools/` (shared with the Alpine tools images: the packages, versions and
licences are the same; the CPython build and its distribution packages are Debian's, listed by `scan.sh`). The
run image holds the admin's Claude Code binary too, so it is built and kept like this one: by the admin, never
redistributed by PipesHub.

Conditions PipesHub follows when it runs the binary (Anthropic, "Can customers offer Claude Code in their
products?"): the binary is not modified; PipesHub removes, disables or restricts none of its sign-in methods
(an administrator may, through Claude Code's own managed settings); model usage is billed to the customer's
own Anthropic API key or cloud account (Bedrock, Vertex), reached through the customer's PipesHub model
gateway; PipesHub does not pay for, resell or intermediate Claude usage.
