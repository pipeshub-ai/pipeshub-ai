---
name: data-analysis
description: "Use this skill whenever the user asks you to load, clean, aggregate, join, or otherwise analyze tabular data (csv, xlsx, json, or a database export) — 'what's the total revenue by region', 'clean up this dataset', 'join these two files on customer id'. Establishes a verification discipline so reported numbers are always numbers the code actually printed, never numbers you inferred or estimated."
metadata:
  agent-loop:
    version: 1.0.0
    category: data
    subcategory: analysis
    tags: [data, pandas, analysis, cleaning]
    status: active
    source: builtin
    pack_name: data-analysis
    pack_version: 1.0.1
---

# Data analysis with pandas

`pandas`, `numpy`, and `scipy` are already installed in the sandbox — no `install_packages` call needed for any workflow below.

## Loading

- Pin dtypes on load rather than letting pandas infer them, when you know the schema — `pd.read_csv(path, dtype={"customer_id": str, "amount": float})`. Inference is usually right but silently wrong in a specific, dangerous way for ID-like columns: a numeric-looking ID (e.g. `"00123"`) inferred as `int64` loses its leading zeros.
- Parse date columns explicitly (`parse_dates=["order_date"]` or `pd.to_datetime(df["col"])` post-load) rather than leaving them as strings — string dates sort and filter lexicographically, not chronologically, which silently produces wrong "most recent N" or date-range results.
- For Excel sources with multiple sheets, always pass `sheet_name=` explicitly (or inspect `pd.ExcelFile(path).sheet_names` first) — `read_excel`'s default of the first sheet is a common source of "the numbers don't match" bugs when the data you actually need is on a different sheet.

## Cleaning

- Check for nulls (`df.isnull().sum()`) and duplicates (`df.duplicated().sum()`) before any aggregation — decide explicitly whether to drop, fill, or flag them, rather than letting them silently skew a `sum`/`mean` (pandas aggregations skip NaN by default, which is not always the right call — a "total" that silently excludes rows with a missing value is a different number than "total of all rows", and the user should know which one they're getting).
- Coerce types explicitly after cleaning (`pd.to_numeric(df["col"], errors="coerce")`) rather than assuming a column that "looks numeric" already is — mixed-type columns from a manually-maintained spreadsheet are common, and a silent string comparison instead of a numeric one produces wrong sort/filter results without raising any error.
- After any cleaning step that drops or modifies rows, print the before/after row count — a cleaning step that unexpectedly drops 80% of the data is a bug, not a result, and should be caught immediately rather than discovered after the whole analysis is built on the reduced dataset.

## Aggregation, joins, pivots

- `groupby(...).agg(...)` for aggregates; always specify the aggregation function explicitly per column (`.agg({"amount": "sum", "order_id": "count"})`) rather than a bare `.sum()` across the whole frame, which silently includes every numeric column whether or not it makes sense to sum it.
- For joins (`pd.merge`), always specify `how=` explicitly (don't rely on the `"inner"` default when the user's intent might be `"left"`/`"outer"`) and check the resulting row count against expectations — an unexpected row-count change after a merge (either fewer rows than the left frame, or way more) almost always means a many-to-many relationship you didn't account for, or a key-matching problem (mismatched types, whitespace, or casing in the join column).
- `pivot_table` for cross-tabulations; pass `fill_value=0` explicitly when a missing combination should read as zero rather than `NaN`, since which one is correct depends on the question being asked.

## Verification discipline (do this before reporting any number)

1. **Always run `df.info()` and `df.describe()` (or `.value_counts()` for categorical columns) before analyzing** — this catches wrong dtypes, unexpected nulls, and outliers early, before they propagate into a wrong conclusion.
2. **Cross-check any headline number by computing it a second, independent way** — e.g. if you report a total via `groupby().sum()`, also sanity-check it against `df["amount"].sum()` on the ungrouped frame (should match), or against a manual filter-and-sum on a known subset. Two different code paths agreeing is meaningfully more trustworthy than one path you didn't double-check.
3. **Never report a number your code didn't actually print.** If you're describing a trend or a comparison, make sure the specific figures you state in your response are ones that appeared in your program's printed output — do not round, estimate, or extrapolate a number that wasn't literally computed and displayed.
