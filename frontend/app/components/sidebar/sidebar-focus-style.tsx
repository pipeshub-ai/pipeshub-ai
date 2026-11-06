'use client';

/**
 * The one focus ring for sidebar controls that are not Radix's own accent ring: chat rows,
 * the Recents toggle, the profile link and the workspace switcher (Radix paints a soft gray
 * button's ring in gray). Add `SIDEBAR_FOCUS_CLASS` to the control; the rule is in the page
 * whenever a sidebar is.
 */
export const SIDEBAR_FOCUS_CLASS = 'sidebar-focus-ring';

/**
 * Hover and press for a rail row built on a Radix ghost button (the chat sidebar's nav rows).
 * Radix paints them with the gray alpha steps, as strong as the selected fill or stronger; every
 * rail row uses the subtle fill instead, one step lighter than selected. A selected row's inline
 * fill wins over it.
 */
export const SIDEBAR_ROW_TINT_CLASS = 'sidebar-row-tint';

const SIDEBAR_FOCUS_CSS = `
.${SIDEBAR_FOCUS_CLASS}:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: -1px;
}
@media (hover: hover) {
  .${SIDEBAR_ROW_TINT_CLASS}:hover {
    background-color: var(--surface-subtle);
  }
}
.${SIDEBAR_ROW_TINT_CLASS}:active {
  background-color: var(--surface-subtle);
}`;

/** React hoists and de-duplicates a style with an href, so the rule is in the page once however often it renders. */
export function SidebarFocusStyle() {
  return (
    <style href="sidebar-focus-ring" precedence="medium">
      {SIDEBAR_FOCUS_CSS}
    </style>
  );
}
