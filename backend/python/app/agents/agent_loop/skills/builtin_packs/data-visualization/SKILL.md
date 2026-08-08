---
name: data-visualization
description: "Use this skill whenever the user asks for a chart, graph, plot, or visualization of data — 'chart this by month', 'show me a breakdown by category', 'visualize the trend'. Covers picking the right chart type for the question being asked, labeling/readability rules, and which plotting library to reach for. Pair with data-analysis for the underlying data prep."
metadata:
  agent-loop:
    version: 1.0.0
    category: data
    subcategory: visualization
    tags: [data, visualization, matplotlib, plotly, charts]
    status: active
    source: builtin
    requires: [data-analysis]
    pack_name: data-visualization
    pack_version: 1.0.0
---

# Data visualization

`matplotlib`, `seaborn`, `plotly`, and `kaleido` are already installed in the sandbox — no `install_packages` call needed for any workflow below.

## Choosing a chart type

Pick the chart type from the *question being asked*, not from habit — a bar chart is not the universal default:

| Question shape | Chart type |
|---|---|
| Comparing a metric across categories ("revenue by region") | Bar chart (horizontal if category labels are long) |
| Trend over time ("revenue by month") | Line chart |
| Distribution of a single variable ("how are order sizes distributed") | Histogram, or box plot for comparing distributions across groups |
| Relationship between two numeric variables ("does price correlate with rating") | Scatter plot |
| Composition of a whole ("market share by segment") | Stacked bar chart — prefer this over a pie chart once there are more than ~4-5 slices, since angle/area comparisons get hard to read past that; a pie chart is defensible for 2-4 slices where the "parts of a whole" framing is the entire point |

If the question doesn't clearly map to one of these, default to a bar or line chart (whichever fits the data shape) rather than reaching for something more exotic — a chart the user immediately understands beats a more "interesting" one they have to puzzle over.

## Readability rules (apply to every chart)

- **Always set a title and axis labels with units** (`"Revenue ($K)"`, not just `"Revenue"`) — a chart with unlabeled axes forces the viewer to guess what they're looking at.
- **Only add a legend when there's more than one series/category to distinguish.** A legend on a single-series chart is clutter.
- **Never leave x-axis labels rotated to the point of being hard to read.** If category names are long, use a horizontal bar chart instead of rotating vertical bar labels 90 degrees.
- **Use a colorblind-safe palette** — matplotlib's `"viridis"`/`"cividis"` colormaps or seaborn's `"colorblind"` palette, rather than a default red/green distinction as the only signal between two series.
- **Sort categorical bar charts by value (descending), not alphabetically**, unless the categories have a natural order (months, ordinal ratings) — alphabetical order makes it harder to spot the biggest/smallest category at a glance.

## Library choice

- **`matplotlib`/`seaborn` → static PNG.** The default choice for a chart that's going into a report, an email, or anywhere it just needs to be an image. Save via `plt.savefig("chart.png", dpi=150, bbox_inches="tight")` — the `bbox_inches="tight"` avoids clipped axis labels, a common failure mode. Saved files surface to the user automatically as artifacts; you don't need to do anything extra to hand them over.
- **`plotly` (+ `kaleido` for static export) → when the user wants interactivity** (hover tooltips, zoom/pan) or explicitly asks for an HTML output. Export interactive output via `fig.write_html("chart.html")`; if a static image is needed instead, `fig.write_image("chart.png")` (uses `kaleido` under the hood).
- Default to `matplotlib`/`seaborn` unless the user's request specifically implies interactivity or a web-embeddable artifact — it's the lighter-weight choice and covers the vast majority of "make me a chart" requests.

## Before finishing

Look at what you actually plotted against what was asked — a common failure mode is generating a technically-valid chart of the wrong slice of data (e.g. totals instead of averages, or the wrong grouping column) because a `data-analysis` step upstream computed something adjacent to, but not exactly, what was requested. Re-read the user's question once more against the chart's title and axes before presenting it.
