---
name: file-conversion
description: "Use this skill when the user asks to convert a file from one format to another (e.g. csv to xlsx, xlsx to json, pdf text to csv) and you're not sure which library handles that specific pair, or whether it's supported in this environment at all. Dispatches to the right library per format pair and explicitly lists conversions that are NOT supported here so you don't attempt one that will silently fail or produce a wrong result."
metadata:
  agent-loop:
    version: 1.0.0
    category: documents
    subcategory: conversion
    tags: [conversion, csv, json, pdf, docx]
    status: active
    source: builtin
    pack_name: file-conversion
    pack_version: 1.1.0
---

# File format conversion

## Supported conversions

| From | To | Default (Node) | Fallback (Python) |
|------|-----|-----------------|--------------------|
| csv | xlsx / json / html | `papaparse` (parse to array of objects) → `exceljs` (xlsx) / `JSON.stringify` (json) / a hand-rolled `<table>` loop (html — no library needed for this) | `pandas.read_csv` → `.to_excel` / `.to_json` / `.to_html` — reach for this when the conversion needs real reshaping (pivots, group-by, dtype coercion) along the way, not as a first choice |
| xlsx | csv / json / html | `exceljs` (read) → `papaparse.unparse` (csv) / `JSON.stringify` (json) / hand-rolled html | `pandas.read_excel` → `.to_csv` / `.to_json` / `.to_html` |
| json (tabular/list-of-records shape) | csv / xlsx | `JSON.parse` → `papaparse.unparse` (csv) / `exceljs` (xlsx) | `pandas.read_json` → `.to_csv` / `.to_excel` |
| image (png/jpg/webp/etc.) | different image format, resize, basic edits | `sharp` (`.toFormat(...)`, `.resize(...)`) | `Pillow` (`Image.open(...).save(new_path)`) |
| docx | plain text | — (no solid Node equivalent for structured docx parsing) | `python-docx` / `docx2python` — extract paragraph and table text |
| pdf | plain text / csv (tables) | — (no solid Node equivalent for table-aware extraction) | `pdfplumber` — `extract_text()` / `extract_tables()` → build a DataFrame if tabular |

For plain csv/xlsx/json reshuffling, **default to the Node path** (an array of plain objects is the common intermediate, playing the same role `pandas.DataFrame` plays on the Python side) per this environment's Node-first policy — reach for `pandas` only when the conversion needs real data reshaping along the way. docx/pdf extraction and structure-aware parsing have no solid Node equivalent here, so those stay Python regardless of format on the other end.

## NOT supported in this environment

These all require a document-rendering engine (LibreOffice) that is not installed in the sandbox yet. Tell the user directly rather than attempting a workaround (e.g. do not try to fake a "pdf" by screenshotting text, and do not claim success on a conversion you didn't actually perform):

- `docx` → `pdf`
- `pptx` → `pdf`
- `doc` (legacy binary Word format) → `docx`
- `xlsx` → `pdf`
- Any conversion that requires rendering/rasterizing a document (visual layout to image)

## Choosing between "extract data" and "convert format"

If the user's underlying goal is "get the numbers out of this PDF/docx into a spreadsheet" rather than literally "convert this file", extracting the relevant data and writing exactly the columns/rows they need is usually a better outcome than a generic whole-file conversion — ask yourself which one they actually need before picking an approach.
