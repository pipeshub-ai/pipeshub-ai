# 04 — Template-branded status update deck

## User message

> [User attaches `acme-brand-template.pptx`]
> Fill this template with our weekly engineering status update. Key points:
> 14 PRs merged, 2 production incidents (both resolved under 15 min), on
> track for the Feb release, blocked on InfoSec review for the new auth flow.
> Include a summary slide with 4 stats and a bullet list for the main items.

## What "great" looks like

- Uses the slide layouts and master from the uploaded template. Brand colors
  preserved (do not override with the skill's defaults).
- Title slide uses the template's "Title Slide" layout with placeholders filled.
- Content slides alternate between the template's "Title and Content" and
  "Two Content" layouts.
- Stat slide fits on one slide using the template's layout (not a new
  freeform layout that breaks brand).
- Calls `coding_sandbox.ingest_template` first to introspect layouts.

## Common failure modes to watch for

- Ignores the template and ships a fresh blue deck.
- Overwrites the theme colors to match the design skill's palette instead of
  the template's.
- Uses `pptxgenjs` (new deck) instead of `python-pptx` (edit template).
