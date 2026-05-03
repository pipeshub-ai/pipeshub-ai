# Radix Spacing Token Audit — `/knowledge-base` Components

**Radix scale:**
| Token | Value |
|-------|-------|
| `--space-1` | 4px |
| `--space-2` | 8px |
| `--space-3` | 12px |
| `--space-4` | 16px |
| `--space-5` | 20px |
| `--space-6` | 24px |
| `--space-7` | 28px |
| `--space-8` | 32px |
| `--space-9` | 36px |

**Rules applied:**
- Only `px` values flagged (%, rem, em, vh/vw skipped)
- Properties audited: `padding`, `margin`, `width`, `height`, `gap`, `borderRadius`
- Skipped: `border-width` (1px separators/dividers), `font-size`, `z-index`, `lineHeight`
- Skipped: values already using `var(--space-*)` tokens
- Skipped: dynamic/computed constants (e.g. `TREE_BASE_PADDING + indent`) — need separate review

**Match Types:**
- `Exact` — a Radix `--space-*` token exists for this value
- `Closest` — no exact match; nearest Radix token used (delta noted)
- `No token` — value is design-specific and not mappable to any Radix token

---

## Audit Table

| Component | File Path | Line # | CSS Property | Current Value | Radix Variable | Match Type |
|-----------|-----------|--------|--------------|---------------|----------------|------------|
| GridCard | `components/kb-grid-view.tsx` | 292 | height | 156px | — | No token |
| GridCard | `components/kb-grid-view.tsx` | 302 | paddingLeft | 8px | `--space-2` | Exact |
| GridCard | `components/kb-grid-view.tsx` | 303 | paddingRight | 16px | `--space-4` | Exact |
| GridCard | `components/kb-grid-view.tsx` | 304 | paddingTop | 16px | `--space-4` | Exact |
| GridCard | `components/kb-grid-view.tsx` | 305 | paddingBottom | 16px | `--space-4` | Exact |
| GridCard checkbox row | `components/kb-grid-view.tsx` | 322 | paddingTop | 4px | `--space-1` | Exact |
| GridCard checkbox row | `components/kb-grid-view.tsx` | 323 | paddingBottom | 4px | `--space-1` | Exact |
| GridCard folder icon | `components/kb-grid-view.tsx` | 351 | width | 24px | — | No token |
| GridCard folder icon | `components/kb-grid-view.tsx` | 351 | height | 24px | — | No token |
| GridCard file icon | `components/kb-grid-view.tsx` | 355 | width | 24px | — | No token |
| GridCard file icon | `components/kb-grid-view.tsx` | 355 | height | 24px | — | No token |
| GridCard actions | `components/kb-grid-view.tsx` | 369 | width | 80px | — | No token |
| Rename input | `components/kb-grid-view.tsx` | 464 | padding | 2px 6px | `--space-1` / `--space-2` | Closest (6px → `--space-2`, −2px delta) |
| File name text | `components/kb-grid-view.tsx` | 488 | padding | 2px 6px | `--space-1` / `--space-2` | Closest (6px → `--space-2`, −2px delta) |
| Empty badge | `components/kb-grid-view.tsx` | 503 | padding | 4px 8px | `--space-1` / `--space-2` | Exact |
| Grid content area | `components/kb-grid-view.tsx` | 595 | padding | 16px | `--space-4` | Exact |
| Grid container | `components/kb-grid-view.tsx` | 603 | gap | 12px | `--space-3` | Exact |
| Pagination flex | `components/kb-grid-view.tsx` | 632 | padding | 8px 16px | `--space-2` / `--space-4` | Exact |
| Page number box | `components/kb-grid-view.tsx` | 662 | padding | 4px 12px | `--space-1` / `--space-3` | Exact |
| Page number box | `components/kb-grid-view.tsx` | 665 | minWidth | 32px | — | No token |
| Pagination separator | `components/kb-grid-view.tsx` | 690 | height | 20px | — | No token |
| Rows/page selector | `components/kb-grid-view.tsx` | 700 | padding | 4px 8px | `--space-1` / `--space-2` | Exact |
| TableHeaderCell btn | `components/kb-list-view.tsx` | 71 | padding | 0 8px | `--space-2` | Exact |
| TableHeaderCell flex | `components/kb-list-view.tsx` | 101 | padding | 0 8px | `--space-2` | Exact |
| TableRow | `components/kb-list-view.tsx` | 296 | height | 60px | — | No token |
| Checkbox flex | `components/kb-list-view.tsx` | 323 | width | 38px | — | No token |
| File name flex | `components/kb-list-view.tsx` | 338 | padding | 0 8px | `--space-2` | Exact |
| Hidden span | `components/kb-list-view.tsx` | 366 | padding | 2px 6px | `--space-1` / `--space-2` | Closest (6px → `--space-2`, −2px delta) |
| Rename input | `components/kb-list-view.tsx` | 390 | padding | 2px 6px | `--space-1` / `--space-2` | Closest (6px → `--space-2`, −2px delta) |
| Empty badge | `components/kb-list-view.tsx` | 424 | padding | 4px 8px | `--space-1` / `--space-2` | Exact |
| Status flex | `components/kb-list-view.tsx` | 435 | width | 60px | — | No token |
| Status flex | `components/kb-list-view.tsx` | 435 | padding | 0 8px | `--space-2` | Exact |
| Source flex | `components/kb-list-view.tsx` | 445 | width | 70px | — | No token |
| Source flex | `components/kb-list-view.tsx` | 445 | padding | 0 8px | `--space-2` | Exact |
| Size flex | `components/kb-list-view.tsx` | 463 | width | 89px | — | No token |
| Size flex | `components/kb-list-view.tsx` | 463 | padding | 0 8px | `--space-2` | Exact |
| Created flex | `components/kb-list-view.tsx` | 473 | width | 147px | — | No token |
| Created flex | `components/kb-list-view.tsx` | 473 | padding | 0 8px | `--space-2` | Exact |
| Updated flex | `components/kb-list-view.tsx` | 483 | width | 146px | — | No token |
| Updated flex | `components/kb-list-view.tsx` | 483 | padding | 0 8px | `--space-2` | Exact |
| Actions flex | `components/kb-list-view.tsx` | 493 | width | 80px | — | No token |
| Actions flex | `components/kb-list-view.tsx` | 493 | padding | 0 8px | `--space-2` | Exact |
| Table header row | `components/kb-list-view.tsx` | 574 | height | 36px | `--space-9` | Exact |
| Checkbox header flex | `components/kb-list-view.tsx` | 585 | width | 38px | — | No token |
| Checkbox header flex | `components/kb-list-view.tsx` | 585 | padding | 0 8px | `--space-2` | Exact |
| Pagination flex | `components/kb-list-view.tsx` | 650 | padding | 8px 16px | `--space-2` / `--space-4` | Exact |
| Page number box | `components/kb-list-view.tsx` | 680 | padding | 4px 12px | `--space-1` / `--space-3` | Exact |
| Page number box | `components/kb-list-view.tsx` | 683 | minWidth | 32px | — | No token |
| Pagination separator | `components/kb-list-view.tsx` | 708 | height | 20px | — | No token |
| Rows/page selector | `components/kb-list-view.tsx` | 718 | padding | 4px 8px | `--space-1` / `--space-2` | Exact |
| Breadcrumb input | `components/header.tsx` | 111 | padding | 2px 6px | `--space-1` / `--space-2` | Closest (6px → `--space-2`, −2px delta) |
| Breadcrumb dropdown | `components/header.tsx` | 244 | padding | 2px 6px | `--space-1` / `--space-2` | Closest (6px → `--space-2`, −2px delta) |
| Header | `components/header.tsx` | 297 | height | 40px | — | No token |
| Header | `components/header.tsx` | 298 | padding | 0 12px | `--space-3` | Exact |
| FilterBar | `components/filter-bar.tsx` | 203 | minHeight | 40px | — | No token |
| FilterBar | `components/filter-bar.tsx` | 203 | padding | 6px 16px | `--space-2` / `--space-4` | Closest (6px → `--space-2`, −2px delta) |
| Clear filter | `components/filter-bar.tsx` | 291 | height | 26px | — | No token |
| Clear filter | `components/filter-bar.tsx` | 291 | padding | 0 8px | `--space-2` | Exact |
| Expand icon | `components/kb-sidebar.tsx` | 54 | marginRight | 4px | `--space-1` | Exact |
| Folder icon | `components/kb-sidebar.tsx` | 75 | marginRight | 4px | `--space-1` | Exact |
| Sidebar | `components/kb-sidebar.tsx` | 151 | width | 233px | — | No token |
| Sidebar header | `components/kb-sidebar.tsx` | 162 | padding | 8px | `--space-2` | Exact |
| Sidebar header | `components/kb-sidebar.tsx` | 163 | height | 56px | — | No token |
| Back button | `components/kb-sidebar.tsx` | 170 | padding | 8px | `--space-2` | Exact |
| All button box | `components/kb-sidebar.tsx` | 191 | padding | 0 8px | `--space-2` | Exact |
| Scrollable content | `components/kb-sidebar.tsx` | 222 | padding | 16px 8px | `--space-4` / `--space-2` | Exact |
| Created By You section | `components/kb-sidebar.tsx` | 226 | marginBottom | 24px | `--space-6` | Exact |
| Section header | `components/kb-sidebar.tsx` | 233 | padding | 8px 12px | `--space-2` / `--space-3` | Exact |
| Action bar | `components/selection-action-bar.tsx` | 40 | padding | 8px 8px 8px 12px | `--space-2` / `--space-3` | Exact |
| Action bar | `components/selection-action-bar.tsx` | 42 | height | 42px | — | No token |
| SearchBar | `components/search-bar.tsx` | 41 | padding | 4px 12px 4px 8px | `--space-1` / `--space-3` / `--space-2` | Exact |
| SearchBar inner | `components/search-bar.tsx` | 53 | padding | 0 8px | `--space-2` | Exact |
| Dropdown menu | `components/item-action-menu.tsx` | 62 | minWidth | 120px | — | No token |
| Loading state | `sidebar/index.tsx` | 98 | padding | 8px | `--space-2` | Exact |
| Empty state | `sidebar/index.tsx` | 293 | padding | 8px 24px | `--space-2` / `--space-6` | Exact |
| Empty state | `sidebar/index.tsx` | 328 | padding | 8px 24px | `--space-2` / `--space-6` | Exact |
| More connectors gap | `sidebar/all-records-mode.tsx` | 218 | gap | 8px | `--space-2` | Exact |
| More connectors | `sidebar/all-records-mode.tsx` | 220 | marginBottom | 8px | `--space-2` | Exact |
| Flex container | `sidebar/all-records-mode.tsx` | 228 | marginTop | 4px | `--space-1` | Exact |
| Button | `sidebar/all-records-mode.tsx` | 240 | paddingLeft | 12px | `--space-3` | Exact |
| Empty state | `sidebar/collections-mode.tsx` | 169 | padding | 0 8px | `--space-2` | Exact |
| Section children | `sidebar/section.tsx` | 112 | marginTop | 4px | `--space-1` | Exact |
| Section header | `sidebar/section.tsx` | 115 | padding | 8px 24px | `--space-2` / `--space-6` | Exact |
| Empty state | `sidebar/section.tsx` | 151 | padding | 8px 24px | `--space-2` / `--space-6` | Exact |
| Empty state | `sidebar/section.tsx` | 191 | padding | 8px 24px | `--space-2` / `--space-6` | Exact |
| Folder tree item | `sidebar/section-element/folder-tree-item.tsx` | 161 | paddingLeft | 10px | `--space-3` | Closest (`--space-3`=12px, +2px delta) |
| Folder tree chevron | `sidebar/section-element/folder-tree-item.tsx` | 194 | marginRight | 4px | `--space-1` | Exact |
| Icon margin | `sidebar/section-element/items.tsx` | 57 | marginRight | 4px | `--space-1` | Exact |
| Icon margin | `sidebar/section-element/items.tsx` | 94 | marginRight | 4px | `--space-1` | Exact |
| Button padding | `sidebar/section-element/items.tsx` | 180 | paddingLeft | 12px | `--space-3` | Exact |
| Item gap | `sidebar/section-element/items.tsx` | 182 | gap | 8px | `--space-2` | Exact |
| Folder item | `components/dialogs/move-folder-sidebar.tsx` | 68 | height | 32px | — | No token |
| Folder item flex | `components/dialogs/move-folder-sidebar.tsx` | 81 | height | 32px | — | No token |
| Folder item flex | `components/dialogs/move-folder-sidebar.tsx` | 83 | paddingRight | 12px | `--space-3` | Exact |
| Dialog content | `components/dialogs/move-folder-sidebar.tsx` | 385 | padding | 16px | `--space-4` | Exact |
| Root item flex | `components/dialogs/move-folder-sidebar.tsx` | 408 | paddingRight | 12px | `--space-3` | Exact |
| Empty state | `components/dialogs/move-folder-sidebar.tsx` | 487 | padding | 32px | `--space-8` | Exact |
| Drop zone (conditional) | `components/dialogs/upload-data-sidebar.tsx` | 240 | padding | 32px / 16px | `--space-8` / `--space-4` | Exact |
| Flex header | `components/dialogs/upload-data-sidebar.tsx` | 449 | padding | 8px 8px 8px 16px | `--space-2` / `--space-4` | Exact |
| Box content | `components/dialogs/upload-data-sidebar.tsx` | 490 | padding | 16px | `--space-4` | Exact |
| File/folder section | `components/dialogs/upload-data-sidebar.tsx` | 502 | padding | 16px | `--space-4` | Exact |
| Folder section | `components/dialogs/upload-data-sidebar.tsx` | 559 | padding | 16px | `--space-4` | Exact |
| Dialog content | `components/dialogs/create-folder-dialog.tsx` | 70 | padding | 24px | `--space-6` | Exact |
| Flex header | `components/dialogs/replace-file-dialog.tsx` | 172 | padding | 8px 8px 8px 16px | `--space-2` / `--space-4` | Exact |
| Box content | `components/dialogs/replace-file-dialog.tsx` | 201 | padding | 16px | `--space-4` | Exact |
| Box display | `components/dialogs/replace-file-dialog.tsx` | 214 | padding | 4px 12px | `--space-1` / `--space-3` | Exact |
| File icon container | `components/dialogs/replace-file-dialog.tsx` | 246 | width | 36px | — | No token |
| File icon container | `components/dialogs/replace-file-dialog.tsx` | 247 | height | 36px | — | No token |
| Replacement file item | `components/dialogs/replace-file-dialog.tsx` | 330 | padding | 12px | `--space-3` | Exact |
| Replacement icon | `components/dialogs/replace-file-dialog.tsx` | 340 | width | 36px | — | No token |
| Replacement icon | `components/dialogs/replace-file-dialog.tsx` | 340 | height | 36px | — | No token |
| Drop zone | `components/dialogs/replace-file-dialog.tsx` | 405 | padding | 32px | `--space-8` | Exact |
| Breadcrumb input | `components/header/breadcrumb-item.tsx` | 87 | padding | 2px 6px | `--space-1` / `--space-2` | Closest (6px → `--space-2`, −2px delta) |
| Selection bar position | `page.tsx` | 2109 | bottom | 24px | `--space-6` | Exact |

---

## Summary

| Match Type | Count |
|------------|-------|
| Exact | 72 |
| Closest | 7 |
| No token | 26 |
| **Total** | **105** |

### Closest matches (no exact Radix token)

| Value | Context | Nearest Token | Delta |
|-------|---------|---------------|-------|
| 6px | `2px 6px` padding (rename inputs, breadcrumb) | `--space-2` (8px) | −2px |
| 2px | `2px 6px` padding (rename inputs, breadcrumb) | `--space-1` (4px) | −2px |
| 10px | `folder-tree-item.tsx` paddingLeft | `--space-3` (12px) | +2px |
| 6px | `filter-bar.tsx` vertical padding | `--space-2` (8px) | −2px |

### No-token dimensions (design-specific fixed sizes)

These are intentional layout/sizing values, not spacing tokens:

- **Icon sizes:** 24px, 36px
- **Column widths:** 38px, 60px, 70px, 80px, 89px, 146px, 147px
- **Container heights:** 20px (separator), 26px, 32px, 40px, 42px, 56px, 60px, 156px
- **Layout widths:** 80px (actions column), 120px (dropdown minWidth), 233px (sidebar width)
- **Dialog dimensions:** 32px (page number minWidth), 36px (file icon containers)

### Excluded — dynamic constants (need separate review)

Several files compute spacing via template literals referencing constants. The constant definitions should be audited and replaced with token values at the source:

- `${TREE_BASE_PADDING + indent}px` — `kb-sidebar.tsx`, `folder-tree-item.tsx`, `items.tsx`, `move-folder-sidebar.tsx`
- `${SECTION_PADDING_BOTTOM}px` — `all-records-mode.tsx`, `collections-mode.tsx`, `section.tsx`
- `${SECTION_CONTENT_MARGIN_TOP}px` — `all-records-mode.tsx`
- `${KB_SECTION_HEADER_MARGIN_BOTTOM}` — `collections-mode.tsx`, `section.tsx`
