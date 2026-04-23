# 01 — Quarterly Business Review deck

## User message

> Build a 10-slide quarterly business review deck for Q4 FY25. Revenue grew
> from $58M in Q3 to $67M in Q4 (+16%). New logos went from 24 to 32. NRR held
> at 124%. Three major launches: Enterprise SSO, Audit Log GA, Streaming
> Exports. Main risks: pipeline coverage below 3x for enterprise, two at-risk
> accounts >$1M ARR. Use a dark executive theme.

## What "great" looks like

- Dark title slide (primary navy), large title, subtitle, date, eyebrow label.
- A "By the numbers" stat-grid slide with 4 big stats (+16%, 124%, 32, 4.8).
- A two-column "Revenue trajectory" slide with bullets on the left and a bar
  chart on the right using palette colors (not pptxgenjs default blue).
- An icon-rows "What shipped" slide with three rows (SSO, Audit, Streaming).
- A before/after two-column "Risks" slide.
- A timeline slide with 4 quarterly milestones.
- A dark closing slide that mirrors the title slide with CTA.
- Consistent footer showing "Q4 FY25 Review  |  N/10" across all content
  slides.
- Zero unicode bullets, zero thin-line-under-the-title accents.

## Common failure modes to watch for

- All-text slides with no charts / icons / callouts.
- Same bulleted layout on every slide.
- Navy + centered body text (should be left-aligned).
- Chart uses default pptxgenjs colors instead of the palette.
