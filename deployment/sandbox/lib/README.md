# pipeshub sandbox helper libraries

This directory contains two internal TypeScript libraries that are baked
into the sandbox Docker image and made available to agent-authored code via
normal `npm` module resolution:

- `pipeshub-slides` — high-level `Deck` primitives built on `pptxgenjs`.
- `pipeshub-docs` — high-level `Report` primitives built on `docx-js`.

Both are designed to be imported from agent-generated TypeScript like any
other npm package:

```ts
import { Deck, THEMES } from "pipeshub-slides";
import { Report, THEMES as DOC_THEMES } from "pipeshub-docs";
```

## Why these exist

The agent is terrible at low-level coordinate math and PowerPoint /
Word XML, but very good at calling named functions. These libraries expose
primitives like `titleSlide`, `twoColumn`, `statGrid`, `iconRows`,
`timeline`, `coverPage`, `table`, `bulletList`, etc. — each of which applies
the chosen theme's palette, fonts, margins, and spacing automatically. The
agent can't produce low-contrast or misaligned output by accident because
those decisions are no longer the agent's to make.

See `backend/python/app/agents/actions/coding_sandbox/skills/pptx.md` and
`skills/docx.md` for the full design system that references these libraries.

## Distribution

At Docker build time the contents of each subdirectory are installed into
`/usr/local/lib/pipeshub` inside the sandbox image and symlinked into the
sandbox user's global `node_modules` so `import "pipeshub-slides"` just
works. See `deployment/sandbox/Dockerfile` for the exact install recipe.

## Local development

If you want to modify a helper and re-test against a running sandbox:

1. Edit the source under `src/`.
2. Rebuild the sandbox image:

   ```bash
   docker build -t pipeshub/sandbox:latest deployment/sandbox
   ```

3. Restart the pipeshub service that uses the sandbox.

No separate build step is required — the libraries ship as `.ts` files and
are loaded via `tsx` at runtime.
