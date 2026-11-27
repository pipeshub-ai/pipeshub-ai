# ADR-SKL-02: A generated document's layout is checked before it is delivered, by one checker the packs share

- Related: ADR-SKL-01 (skills a run is offered, pinned by digest), SK1 and SKILLPACKS (packs in the run images), SK-03 (the packs run in every engine's image).

## Context

A manual test (2026-11-10): a sports season report as a PDF. A Codex job (codex 0.160.0, `pxh-codex-tools:0.160.0-ph5`) read `/skills/pdf/SKILL.md` and wrote a pdfkit program. The 5-page PDF it delivered was broken: every text block started at the page's top-left edge and flowed down over other text and dark panels, while the panels and cards sat empty further down (reported as badly formatted).

The program's helper called `doc.text(str, { x, y, width, … })`. pdfkit reads a position only as arguments (`doc.text(text, x, y, options)`; `_initOptions` treats an object first argument as the options and never reads `x`/`y` from it), so each call wrote at the current `doc.x`/`doc.y`. The margins were 0, so that was the page edge. The same program with `doc.text(str, x, y, options)` makes the report as designed (`fixtures/layout/ipl_fixed.*`).

The engine then checked its work with text extraction only (pdftotext, pypdf). Extraction proves the words are in the file and cannot see where they were drawn. The design fault was in the packs: none of them asked for a check of the result as drawn, and none had a way to make one. The pdf pack did not warn of the pitfall either.

## Decision

**D1. One checker, in `office-utils`.** `office-utils/scripts/check_layout.py` checks a `.pdf`, `.pptx` or `.docx`. It uses only libraries every run image has (`pdfplumber`, `pypdfium2`, `numpy`, Pillow, `python-pptx`, `python-docx`), checked image by image. A PDF is rendered page by page to PNG, and the checker flags `off_page`, `outside_margin` (18 pt by default), `text_overlap`, `low_contrast` (under 2:1 against the rendered pixels around the text), `hidden_text` (a shape drawn over it), `empty_box` (a panel or card with no text or graphic inside) and `blank_page`. Each kind it reports comes with a fix. It exits 1 while anything is flagged.

**D2. The packs make it a required step.** The pdf, pptx and docx packs end generation with "Check before you deliver (required)": run the checker, fix, rerun until `layout: clean`, look at the PNGs if your tools can show an image, and tell the person what was checked. `pdf` now `requires: [office-utils]`, so the loop stages the checker with it as docx and pptx already did; an engine has every pinned builtin under `/skills`. The pdf pack opens with the pitfalls, the pdfkit position first.

**D3. What cannot be rendered is said, not faked.** No run image has LibreOffice, so a `.pptx` or `.docx` is not rendered. A deck is checked by its shapes' geometry (off the slide, text boxes over text boxes, blank slides). A document is checked for what Word cannot reflow (tables and images wider than the text width, an empty document). The report's `checked:` line says "not rendered".

**D4. A pack's content changes only with its version.** `BuiltinSkillSeeder.sync` upgrades an org's seeded copy only when `pack_version` changes, and admission pins the seeded content's digest (ADR-SKL-01 D2), so content changed under an old version would never reach an org already seeded. `tests/unit/agents/agent_loop/skills/fixtures/builtin_pack_digests.json` records the pin digest of every pack version; an entry is never rewritten, and the test fails when a pack's content differs from its recorded version's.

**D5. Looking at the pages is extra; the checker is the gate.** Which engines can look:
- Claude Code: its `Read` tool returns a PNG as an image, and the model gateway serves an image in a tool result (Anthropic Messages wire), so a vision model sees the pages.
- Codex 0.160: `view_image` (FIXPDFSKILL-FU-1, below). The Responses wire serves its `input_image` result.
- ACP engines and the loop: whatever their file tool returns. The pack says "if you can view images", so the checker's report is the gate on every engine.

## As built

- Checker: `backend/python/app/agents/agent_loop/skills/builtin_packs/office-utils/scripts/check_layout.py`.
- Release catalog: `contracts/control-plane/catalog/skills.json` and Node's `release-catalog.generated.ts` regenerated (`python -m app.control_plane.catalog.dump --write`); their digests equal the recorded ones.
- Packs: pdf 1.4.0, office-utils 1.1.0, docx 1.2.0, pptx 1.3.0, file-conversion 1.2.0 (a PDF's pages can be rendered; a docx, pptx or xlsx cannot).
- Tests: `tests/unit/agents/agent_loop/skills/test_fixpdfskill_layout_check.py`; Docker `tests/unit/harness/sandbox_broker/test_fixpdfskill_layout_check_docker.py` in every release run image.

- Codex's image viewer (FIXPDFSKILL-FU-1, decided UX-first): `view_image` is on whenever the managed policy hook is rendered, and off without it (`harness_engines/codex/launch.py` `IMAGE_TOOL`). Codex runs under `danger-full-access`, so its own file sandbox confines nothing and `view_image` would read any path the process can (`core/src/tools/handlers/view_image.rs`: the path joined to the cwd, read with the turn's sandbox context). Codex runs `PreToolUse` hooks before every function tool, `view_image` included (`tool_input` `{"path"}`), so the hook's matcher is `Bash|view_image`; `ph policy check --hook` resolves the path in the sandbox through every symlink, and the native-call policy refuses a read that names a file outside `/workspace` (`outside_workspace`). The hook fails closed. Codex also sends only bytes that decode as an image. Each image looked at is an `imageView` item, mapped to a `view_image` step labelled "Looked at an image". The pdf pack (1.4.1) says "a file-reading tool that shows you a PNG, or an image viewer".

## Deviations

- An org that edited a builtin pack keeps its copy (the seeder skips it), and so does not get the check until it takes the new version.
- If an org disables `office-utils`, the pdf pack's checker is not mounted. The pack still tells the engine to check, so the engine says it could not.
- Codex's image viewer is confined by the policy hook only. A hook that Codex kills at its 30 s timeout lets the call run (Codex fails open there; `ph` gives up after 10 s and refuses first). In build mode a concurrent shell could swap a symlink between the check and the read, but that shell can already read the same file.

## Owner amendments

None.

## Status

**Accepted** (2026-11-10), decided by FIX-PDFSKILL from first principles, long term, UX-first. Not yet seen on a human-test stack.
