# PipesHub Block Visualizer

A standalone tool to visualize and debug PipesHub `BlocksContainer` structures from record JSON files.

## Features

- 🔍 **Visual Hierarchy**: See block groups and blocks with clear parent-child relationships
- ✅ **Consistency Checks**: Automatically validates parent_index references, range boundaries, and image URIs
- 🎨 **Type-Colored Cards**: Different colors for different block/group types
- 📊 **Table Rendering**: Tables shown with proper row/column structure, including nested tables inside cells via `cell_details`
- 📄 **PDF Bounding Boxes**: Shows normalized coordinates for PDF-sourced blocks
- 🖼️ **Image Preview**: Inline rendering of base64 and URL images
- 📝 **Markdown Support**: Renders markdown content with proper formatting
- 📈 **Summary Panel**: Quick stats on block/group counts and types

## Installation

```bash
cd tools/viz_blocks
pip install -r requirements.txt
```

## Quick Test

An example record JSON is provided for testing:

```bash
python viz_blocks.py --path example_record.json --open
```

This will generate a visualization showing:
- A text_section group with nested content
- A table group with header and data rows
- An orphan text block
- Citation metadata with bounding boxes

## Usage

### Basic Usage with Org + Record ID

The tool will automatically find the record JSON in your local AppData storage:

```bash
python viz_blocks.py --org ORG_ID --record VIRTUAL_RECORD_ID
```

### Direct Path

If you have a record JSON file in a custom location:

```bash
python viz_blocks.py --path "C:\path\to\record.json"
```

### Options

```bash
--output out.html        # Specify output HTML file path (default: auto-generated)
--open                   # Auto-open in default browser after generation
--no-content             # Show structure/IDs only, skip rendering data fields
--max-text-len 500       # Truncate long text blocks (default: 1000 chars)
```

### Examples

```bash
# Basic visualization
python viz_blocks.py --org abc123 --record def456

# Generate and open in browser
python viz_blocks.py --org abc123 --record def456 --open

# Custom output location
python viz_blocks.py --path record.json --output ~/Desktop/viz.html

# Structure-only mode (faster for large records)
python viz_blocks.py --org abc123 --record def456 --no-content
```

## What Gets Visualized

### Block Groups
- Hierarchy via `parent_index`
- Type and sub-type badges
- Children relationships (both old list and new range formats)
- Nested content with depth-based indentation
- Special rendering for tables, sheets, lists, comments

### Blocks
- Type-specific rendering (text, image, table_row, record_summary)
- Citation metadata (page numbers, bounding boxes for PDFs)
- Format-aware content display (markdown, JSON, plain text)
- Image validation and preview

### Consistency Checks
- ✅ Valid parent_index references
- ✅ Block ranges within bounds
- ✅ Parent-child pointer consistency
- ✅ Image URI validation
- ⚠️ Warnings shown inline on problematic blocks/groups

## Output

The tool generates a **self-contained HTML file** with:
- All CSS embedded inline (no external dependencies)
- Base64 images embedded directly
- Sticky summary panel at top
- Collapsible sections for large structures
- Print-friendly styling

## Troubleshooting

### "Record is compressed" error

Install compression dependencies:
```bash
pip install zstandard msgspec
```

### "No record JSON found"

The tool looks in: `%USERPROFILE%\AppData\PipesHub\{orgId}\PipesHub\records\{virtualRecordId}\*\current\`

Make sure:
1. The record exists in local storage
2. Org ID and virtual record ID are correct
3. You're on the correct Windows user account

### Missing dependencies

Install all requirements:
```bash
pip install -r requirements.txt
```

## Development

The tool is entirely self-contained in `viz_blocks.py` with no external dependencies on the main PipesHub codebase.

To extend or modify:
1. All rendering logic uses the `dominate` library for HTML generation
2. Tree building handles both old and new children formats automatically
3. Add new block/group type renderers in the `render_block_content` function
