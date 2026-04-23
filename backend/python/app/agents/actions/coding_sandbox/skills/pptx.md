# PPTX Design Skill

Use this skill **before writing any code** that produces a `.pptx` file — new
deck, edited template, pitch, investor update, report-out, whatever the user
calls it. Plain white backgrounds with bulleted lists are a tell that the deck
was produced by an AI. Aim higher.

---

## Quick reference — pick the right runtime

| Task                              | Runtime                         | Library            |
|-----------------------------------|---------------------------------|--------------------|
| New deck from scratch             | `execute_typescript`            | `pipeshub-slides` (preferred) or `pptxgenjs` |
| Edit existing `.pptx` template    | `execute_python`                | `python-pptx`      |
| Read / extract content            | `execute_python`                | `python-pptx`      |
| Render deck for visual QA         | `coding_sandbox.render_artifact_preview` | LibreOffice + pdftoppm |

The Node path (`pipeshub-slides` on top of `pptxgenjs`) is the default for new
decks. It produces natively editable PowerPoint shapes and is much easier to
style than `python-pptx`. Use `python-pptx` only when you are editing an
existing deck and need to preserve the user's slide masters.

---

## Step 1: Pick a color palette that fits the topic

Do **not** default to corporate blue. Pick a palette whose feel matches the
content. One color dominates (60–70% of the visual weight), 1–2 supporting
tones (20–30%), one sharp accent (~10%).

| Theme                | Primary            | Secondary         | Accent             |
|----------------------|--------------------|-------------------|--------------------|
| Midnight Executive   | `1E2761` navy      | `CADCFC` ice blue | `FFFFFF` white     |
| Forest & Moss        | `2C5F2D` forest    | `97BC62` moss     | `F5F5F5` cream     |
| Coral Energy         | `F96167` coral     | `F9E795` gold     | `2F3C7E` navy      |
| Warm Terracotta      | `B85042` terracotta| `E7E8D1` sand     | `A7BEAE` sage      |
| Ocean Gradient       | `065A82` deep blue | `1C7293` teal     | `21295C` midnight  |
| Charcoal Minimal     | `36454F` charcoal  | `F2F2F2` off-white| `212121` black     |
| Teal Trust           | `028090` teal      | `00A896` seafoam  | `02C39A` mint      |
| Berry & Cream        | `6D2E46` berry     | `A26769` rose     | `ECE2D0` cream     |
| Sage Calm            | `84B59F` sage      | `69A297` eucalypt | `50808E` slate     |
| Cherry Bold          | `990011` cherry    | `FCF6F5` off-white| `2F3C7E` navy      |

**Sandwich structure:** dark background on title + closing slides, light on
content. Or commit to dark throughout for a premium feel — just don't mix
randomly.

**Commit to a visual motif** — rounded image frames, icons in colored
circles, a thick left-edge accent bar on each slide, numbered tabs in the
corner. Pick ONE and repeat it across every slide.

---

## Step 2: Typography

Default Arial is forgettable. Pick a header font with personality and a clean
body font. Both must exist on Windows + macOS + LibreOffice.

| Header font    | Body font     | Vibe              |
|----------------|---------------|-------------------|
| Georgia        | Calibri       | Editorial         |
| Arial Black    | Arial         | Bold / punchy     |
| Cambria        | Calibri       | Corporate refined |
| Trebuchet MS   | Calibri       | Friendly          |
| Impact         | Arial         | Marketing         |
| Palatino       | Garamond      | Literary          |
| Consolas       | Calibri       | Technical         |

| Element         | Size           |
|-----------------|----------------|
| Slide title     | 36–44 pt bold  |
| Section header  | 20–24 pt bold  |
| Body text       | 14–16 pt       |
| Caption / note  | 10–12 pt muted |

---

## Step 3: Every slide needs a visual element

A slide that is just a title plus bullets is forgettable. Pick one of the
layouts below and stick with 2–3 layouts across the deck — variety matters,
but not a different layout every slide.

### Layout catalogue

- **Title slide** — large title, subtitle, one accent shape or half-bleed
  image. Dark background for impact.
- **Two-column** — text on the left (headline + 3 short lines), chart / image
  / code on the right. 60/40 split looks better than 50/50.
- **Icon + text rows** — 3–5 horizontal rows, each: circled icon on left,
  bold header, one-line description. Great for feature lists.
- **2x2 or 2x3 grid** — four to six cards with a header + short text each.
  Use for pros/cons, pillars, phases.
- **Half-bleed image** — full-height image on one side, content on the other.
  Premium feel.
- **Stat callout** — one gigantic number (72–120 pt) with a small label. One
  per slide. Max impact for a single data point.
- **Before / after** — two columns side-by-side with a clear divider and
  headers. Include an arrow in the middle for direction.
- **Timeline / process flow** — numbered steps connected by arrows or a
  horizontal line, 3–5 steps max per slide.
- **Closing / thank-you slide** — dark background, large title, CTA or
  contact info. Mirrors the title slide.

### Data display

- **Charts** — never default pptxgenjs styling. Apply custom colors from your
  palette, muted axis labels (`64748B`), subtle gridlines (`E2E8F0`), hide
  legend when you have a single series, set `chartArea: { fill: { color: "FFFFFF" }, roundedCorners: true }`.
- **Tables** — subtle row shading (alternate rows `F8FAFC` / `FFFFFF`),
  header row in your primary color with white text, 80 twip top/bottom cell
  padding, no outer borders.

---

## Step 4: Common mistakes to avoid

- **Do NOT put a thin horizontal accent line under the slide title.** That is
  the single biggest "AI generated" tell. Use whitespace or a full-width
  colored band at the top edge instead.
- **Do NOT center body text.** Center only titles and single-line stats;
  left-align everything else.
- **Do NOT use the same layout on every slide.** Alternate at least 2–3
  layouts.
- **Do NOT default to corporate blue.** Pick a palette that fits the topic.
- **Do NOT use low-contrast text.** Dark text on dark backgrounds or light
  text on light backgrounds is unreadable on a projector.
- **Do NOT put a text-only slide in the deck.** Always add a chart, image,
  icon, shape, or callout.
- **Do NOT use unicode bullets (`•`, `\u2022`).** Use the library's native
  bullet API — manual bullets create double-bullets and break indentation.
- **Do NOT skimp on size contrast.** A 14 pt title next to 14 pt body is
  mush. Titles 36 pt+, body 14–16 pt.
- **Do NOT mix gaps.** Pick one of 0.3" / 0.5" and use it consistently for
  gaps between blocks.

---

## Step 5: Using the `pipeshub-slides` helper (recommended)

The helper library applies a theme's palette, fonts, margins, and spacing
automatically so layout math and color consistency are handled for you.

```ts
import { Deck, THEMES } from "pipeshub-slides";
import * as path from "path";

const deck = new Deck({
  theme: THEMES.midnightExecutive,
  layout: "16x9",
  author: "pipeshub agent",
  title: "Q4 FY25 Business Review",
});

deck.titleSlide({
  title: "Q4 FY25",
  subtitle: "Business review",
  eyebrow: "Internal",
});

deck.twoColumn({
  title: "Highlights",
  left: {
    bullets: [
      "Revenue +18% YoY, driven by enterprise",
      "NRR held at 124%",
      "New logo count up 32%",
    ],
  },
  right: {
    chart: {
      type: "bar",
      categories: ["Q1", "Q2", "Q3", "Q4"],
      series: [{ name: "Revenue ($M)", values: [42, 51, 58, 67] }],
    },
  },
});

deck.statGrid({
  title: "Impact",
  stats: [
    { value: "+18%", label: "YoY revenue" },
    { value: "124%", label: "NRR" },
    { value: "32", label: "New logos" },
    { value: "4.8", label: "CSAT" },
  ],
});

deck.iconRows({
  title: "What shipped",
  rows: [
    { icon: "FaRocket", header: "Enterprise SSO", body: "SAML + SCIM GA" },
    { icon: "FaShieldAlt", header: "Audit log", body: "All admin actions" },
    { icon: "FaBolt", header: "Streaming exports", body: "50× faster" },
  ],
});

deck.closing({ title: "Thank you", subtitle: "Questions?" });

await deck.save(path.join(process.env.OUTPUT_DIR!, "q4-review.pptx"));
```

See `pipeshub-slides` type definitions in the sandbox's node_modules for the
complete API — every primitive documents its options.

---

## Step 6: Using `pptxgenjs` directly (when the helper is too restrictive)

```ts
import pptxgen from "pptxgenjs";
const pres = new pptxgen();
pres.layout = "LAYOUT_16x9";           // 10" × 5.625"
pres.title = "Deck title";

const PRIMARY = "1E2761";              // NEVER include the leading '#'
const ACCENT  = "F96167";

const slide = pres.addSlide();
slide.background = { color: "FAFAFA" };

// Full-width top accent band (use a RECTANGLE shape, NOT a horizontal line
// under the title — the line is the AI-generated tell).
slide.addShape(pres.shapes.RECTANGLE, {
  x: 0, y: 0, w: 10, h: 0.25,
  fill: { color: PRIMARY }, line: { type: "none" },
});

slide.addText("Slide title", {
  x: 0.5, y: 0.6, w: 9, h: 0.8,
  fontSize: 40, bold: true, color: PRIMARY, fontFace: "Georgia",
  margin: 0,
});

// Rich text, bullets via the library API — NEVER manual "•"
slide.addText(
  [
    { text: "First point on this slide", options: { bullet: true, breakLine: true } },
    { text: "Second point, kept short",  options: { bullet: true, breakLine: true } },
    { text: "Third point, one line max", options: { bullet: true } },
  ],
  { x: 0.5, y: 1.6, w: 5, h: 3, fontSize: 16, color: "1F2937", paraSpaceAfter: 8 },
);

await pres.writeFile({ fileName: process.env.OUTPUT_DIR + "/deck.pptx" });
```

### Pptxgenjs pitfalls (these corrupt the file or look terrible)

1. **NEVER prefix hex colors with `#`.** Use `"FF0000"`, not `"#FF0000"`.
2. **NEVER encode opacity in the hex string.** `"00000020"` corrupts the
   file. Use the separate `opacity` (or `transparency`) property.
3. **NEVER reuse option objects between `addShape`/`addText` calls.**
   Pptxgenjs mutates in place. Build a factory function that returns a fresh
   object per call.
4. **`bullet: true` only** — never manual `"•"`.
5. **Each presentation = fresh `pptxgen()` instance.** Do not reuse.
6. **Shadow `offset` must be non-negative.** Negative values break the file;
   to cast a shadow upward use `angle: 270` with a positive offset.
7. **`ROUNDED_RECTANGLE` + a rectangular accent bar on the left doesn't
   cover the rounded corner.** Use `RECTANGLE` if you need an accent.

---

## Step 7: Editing an existing `.pptx` template

When the user uploads a template, preserve the master / theme / slide
layouts. Use `python-pptx` for this — it was built for the template use case.

```python
import os
from pptx import Presentation

prs = Presentation(os.environ["TEMPLATE_PATH"])

# Inspect layouts so you know which one to use per slide.
for i, layout in enumerate(prs.slide_layouts):
    print(i, layout.name, [p.placeholder_format.idx for p in layout.placeholders])

# Add a slide from the "Title and Content" layout (index varies per template).
title_layout = prs.slide_layouts[1]
slide = prs.slides.add_slide(title_layout)
slide.shapes.title.text = "Q4 business review"
slide.placeholders[1].text = "Three highlights, in short sentences."

prs.save(os.path.join(os.environ["OUTPUT_DIR"], "edited.pptx"))
```

Call `coding_sandbox.ingest_template` first to get a structured summary of
the template (available layouts, placeholder names, master colors, font
theme) before generating code.

---

## Step 8: Visual QA (required before declaring success)

Your first render is almost never correct. After writing the `.pptx`, call
`coding_sandbox.render_artifact_preview` on it. The tool runs the deck
through LibreOffice and returns one JPEG per slide.

Look at each image and find:

- Text overflowing a shape or running off the slide edge
- Title that wrapped to two lines and now collides with body text below
- Two elements overlapping (text through a shape, lines through words)
- Gaps under 0.3" or over 0.8" between content blocks
- Cards / columns that are not aligned to the same baseline
- Low-contrast text or icons (light on light, dark on dark)
- Leftover placeholder text (`xxxx`, `lorem ipsum`, "Click to add title")
- Visible "AI-generated" tells (thin accent line under title, identical
  layout on every slide, all bullets no visuals)

Fix issues, regenerate, re-preview, repeat. Cap at 3 iterations per tool
call — if you haven't converged by then, change the approach.

---

## Step 9: Final content QA

Before declaring done, scan the extracted text for placeholder leftovers:

```python
# stdout check — no leftover template text
from pptx import Presentation
prs = Presentation(path)
text = "\n".join(
    shape.text_frame.text
    for slide in prs.slides
    for shape in slide.shapes
    if shape.has_text_frame
).lower()
assert not any(marker in text for marker in ("lorem", "ipsum", "xxxx", "click to add"))
```
