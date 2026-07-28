---
name: pptx
description: "Use this skill whenever the user asks for a PowerPoint presentation, a .pptx file, or slides for a talk/pitch/report — creating a new deck from scratch, editing an existing .pptx, or reading/summarizing an existing deck's content. Covers slide layout, color/typography choices, and a content-quality checklist for professional-looking output. Does not cover rendering slides to images for visual QA (see the 'Not supported' section)."
metadata:
  agent-loop:
    version: 1.0.0
    category: documents
    subcategory: powerpoint
    tags: [pptx, powerpoint, slides, presentations]
    status: active
    source: builtin
    requires: [office-utils]
    pack_name: pptx
    pack_version: 1.1.1
---

# PowerPoint presentations (.pptx)

## Quick Reference

| Task | Approach |
|------|----------|
| Create a new deck from scratch | `pptxgenjs` — write a TypeScript program (packages: ["pptxgenjs"]) and run it via your coding tool |
| Edit an existing .pptx | office-utils: unpack → edit XML → pack (see that skill) |
| Read / extract text from a .pptx | `python-pptx` (Python, already installed) |

**Always CREATE decks with `pptxgenjs` in TypeScript — never with `python-pptx`.** `python-pptx` is in this environment only for *reading* existing decks; its creation API produces flat, template-default output and is not the supported path here. Run the TypeScript program via whatever coding surface you have: the `run_code` tool directly (language="typescript", packages=["pptxgenjs"]), or the `coding_agent` delegate — in that case restate these library/language requirements in its goal. `pptxgenjs` is pre-installed in the standard sandbox image, so the install step is an instant no-op there, and the `packages` arg covers any sandbox where it isn't.

## Creating a new deck (pptxgenjs)

Minimal skeleton — `writeFile` is async and MUST be awaited before the program exits, or the file is silently truncated/missing:

```typescript
import PptxGenJS from "pptxgenjs";

async function main() {
  const pptx = new PptxGenJS();
  pptx.defineLayout({ name: "WIDE", width: 13.33, height: 7.5 });
  pptx.layout = "WIDE";

  // Title slide — distinct from content slides.
  const title = pptx.addSlide();
  title.background = { color: "1B2A4A" }; // deliberate palette, not a default
  title.addText("Bug Bash — Last 2 Months", {
    x: 0.8, y: 2.6, w: 11.7, h: 1.2, fontSize: 40, bold: true, color: "FFFFFF",
  });
  title.addText("Leadership summary", { x: 0.8, y: 3.9, w: 11.7, h: 0.6, fontSize: 18, color: "9DB2D0" });

  // Content slide with header accent + bullets.
  const slide = pptx.addSlide();
  slide.addShape("rect", { x: 0, y: 0, w: 13.33, h: 0.9, fill: { color: "1B2A4A" } });
  slide.addText("Key findings", { x: 0.5, y: 0.12, w: 9, h: 0.66, fontSize: 24, bold: true, color: "FFFFFF" });
  slide.addText([
    { text: "12 sessions held across 9 feature areas", options: { bullet: true } },
    { text: "38 bugs filed, 31 resolved", options: { bullet: true } },
  ], { x: 0.7, y: 1.3, w: 11.9, h: 4.5, fontSize: 18, color: "222222" });

  await pptx.writeFile({ fileName: "deck.pptx" });
}

main();
```

Positions/sizes are in inches on the slide layout; colors are hex strings without `#`. `addTable(rows, options)` handles tabular slides; `addImage({ data: "image/png;base64,..." })` embeds charts rendered elsewhere.

Design quality matters as much as technical correctness for a deck; a technically valid presentation with no visual hierarchy still reads as low-effort. Apply this checklist to every deck you generate:

1. **Pick a color palette informed by the topic, not a random default.** One dominant color (used for accents, headers, key numbers) plus one or two neutrals (backgrounds, body text) reads as designed; five unrelated colors reads as generated. A finance deck skews navy/gray; a creative pitch can use a bolder accent — pick deliberately and state your choice in a code comment.
2. **Vary layout across slides — don't reuse the same "title + bullet list" template for all of them.** A title slide, a two-column comparison slide, a big-number/stat slide, and a section-divider slide all look different in `pptxgenjs`'s `addSlide()`/`addText()`/`addShape()`/`addImage()` API. A deck where every slide is identical bullet-point boilerplate is a common failure mode — actively avoid it.
3. **Avoid dense walls of text.** A slide is a visual aid, not a document — 5-7 words per bullet, 3-5 bullets per slide is a reasonable ceiling. If the source content is denser than that, summarize it for the slide and note that the detail lives in a companion doc, rather than shrinking the font to fit everything.
4. **Set explicit font sizes for hierarchy** — title text meaningfully larger (28-40pt) than body text (16-20pt); don't rely on template defaults if you're building shapes/text boxes manually.
5. **Before finishing, run through this checklist against your generated code**: Does every slide have a clear single takeaway? Is there a consistent color/font scheme across all slides? Are any text boxes likely to overflow their bounding box given the content length? Is the title slide distinct from content slides?

## Editing an existing .pptx

Use `office-utils`'s unpack → edit → pack cycle: individual slides live at `ppt/slides/slide1.xml`, `ppt/slides/slide2.xml`, etc. inside the unpacked tree (matching `ppt/_rels/presentation.xml.rels`' slide ordering). Make the smallest edit that achieves the goal — editing one slide's text run rather than regenerating the whole deck preserves the original author's layout/theme/master-slide choices that a from-scratch `pptxgenjs` regeneration would lose.

## Reading a deck

`python-pptx`: `Presentation(path).slides` → each slide's `.shapes` exposes `.text_frame.text` for text content and `.table` for tables. Already installed — no `install_packages` call needed.

## Not supported in this environment

- Rendering slides to images for visual QA requires LibreOffice (`soffice`) + Poppler, neither of which is installed in the sandbox yet. You cannot visually verify a generated deck's layout — rely on the content-quality checklist above and on structural checks (does the file still parse? are text boxes' declared dimensions large enough for their text at the chosen font size?) instead of an actual render.
