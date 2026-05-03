# Dropdown & Pill Input Behaviors

Common interaction patterns shared between `SearchableCheckboxDropdown`, `TagInput`, and similar pill/chip input components.

## Components Using These Patterns

| Component | Location | Purpose |
|-----------|----------|---------|
| `SearchableCheckboxDropdown` | `app/(main)/workspace/components/searchable-checkbox-dropdown.tsx` | Multi-select dropdown with search, checkboxes, and avatar support |
| `TagInput` | `app/(main)/workspace/components/tag-input.tsx` | Freeform text input that converts entries into tag pills |

## Shared Behaviors

### 1. Backspace to Remove Last Pill

When the text input is **empty** and the user presses **Backspace**:

- **TagInput**: First press highlights the last pill. Second press removes it.
- **SearchableCheckboxDropdown**: Single press removes the last selected pill.

### 2. Clear Search Text on Selection

When the user types to filter options and then selects/clicks an item:

- The search query is **cleared immediately** after selection.
- The full unfiltered list becomes visible again.
- This prevents the user from being stuck in a filtered view after picking an option.

### 3. Escape to Close

Pressing **Escape** while the dropdown/input is focused:

- Closes the dropdown list (SearchableCheckboxDropdown).
- Does not remove any selections.

### 4. Click Outside to Close

Clicking outside the component:

- Closes the dropdown list.
- In TagInput, any remaining text in the input is converted to a tag on blur.

### 5. Pill Removal via Close Icon

Each pill/chip displays a small **close (×) icon**:

- Clicking the icon removes that specific pill from the selection.
- The click event is stopped from propagating to the parent (prevents reopening dropdown).

### 6. Auto-Scroll on Selection Change

When pills are added or removed:

- **TagInput**: Scrolls the container to the bottom to keep the latest tag visible.
- **SearchableCheckboxDropdown**: Scrolls the chips row to the right end.

### 7. Dropdown Direction (Auto-detection)

`SearchableCheckboxDropdown` detects available viewport space:

- If there's enough space below the trigger, the dropdown opens **downward**.
- If space is insufficient (< 220px), the dropdown opens **upward**.
- This prevents the dropdown from being clipped by scroll containers (e.g., inside `WorkspaceRightPanel`).

### 8. Focus Management

- Clicking the trigger area focuses the text input.
- After the dropdown is opened, the input receives focus automatically.
- The component scrolls itself into view when opened inside a scrollable container.

## TagInput-Specific Behaviors

These are unique to `TagInput` and not currently in `SearchableCheckboxDropdown`:

| Behavior | Description |
|----------|-------------|
| **Commit on Enter/Comma/Tab/Space** | Typing a delimiter key converts the current text into a tag |
| **Paste support** | Pasted text is split by commas, spaces, and newlines into multiple tags |
| **Duplicate prevention** | Tags with matching values (case-insensitive) are silently ignored |
| **Validation** | Each tag can be validated (e.g., email format); invalid tags show a red style |
| **Click-to-edit** | Clicking a pill moves its value back into the input for editing |
| **Highlight before remove** | Backspace first highlights the last tag, then removes on second press |

## Implementation Notes

- All components use **inline styles** with Radix CSS variables (no Tailwind).
- Pill colors use `var(--slate-a3)` background with `var(--slate-12)` text.
- Close icons use `MaterialIcon` with `name="close"` at 14px.
- Hover effects on dropdown options use `onMouseEnter`/`onMouseLeave` with `var(--slate-a3)`.
