# Radix Spacing Audit — @sidebar Components

**Date:** 2026-04-16  
**Branch:** `radix_styling_fixes`  
**Scope:** All components that back the `app/(main)/@sidebar/**` Next.js parallel-route slots.

---

## Scope Note

The `app/(main)/@sidebar/**` directory contains only thin Next.js slot-wrapper pages that delegate all rendering to imported components. The actual sidebar implementations live in:

- `app/(main)/chat/sidebar/` — Chat sidebar components (primary scope)
- `app/components/sidebar/` — Shared sidebar primitives (`SidebarBase`, `SecondaryPanel`, `constants.ts`, etc.)
- `app/(main)/chat/components/chat-sidebar.tsx` — Legacy chat sidebar (kept for reference)

All files in both directories were audited.

---

## Radix UI Spacing Scale Reference

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

---

## Audit Table

| Component | File Path | Line # | CSS Property | Current Value | Radix Variable | Match Type |
|-----------|-----------|--------|-------------|---------------|----------------|------------|
| `SidebarItem` | `app/(main)/chat/sidebar/sidebar-item.tsx` | 79 | `gap` | `8` (unitless px) | `--space-2` | Exact |
| `ChatSidebarFooter` | `app/(main)/chat/sidebar/footer.tsx` | 43 | `padding` | `8` (unitless px) | `--space-2` | Exact |
| `ChatSidebarFooter` | `app/(main)/chat/sidebar/footer.tsx` | 58 | `height` | `40` (unitless px) | `--space-10` (40px, non-standard) | No token |
| `ChatItemMenu` | `app/(main)/chat/sidebar/chat-item-menu.tsx` | 63 | `padding` | `2` (unitless px) | ½ × `--space-1` | No token |
| `ChatItemMenu` | `app/(main)/chat/sidebar/chat-item-menu.tsx` | 78 | `minWidth` | `140` | — | No token |
| `AgentSidebarItemMenu` | `app/(main)/chat/sidebar/agent-sidebar-item-menu.tsx` | 57 | `padding` | `2` (unitless px) | ½ × `--space-1` | No token |
| `AgentSidebarItemMenu` | `app/(main)/chat/sidebar/agent-sidebar-item-menu.tsx` | 68 | `minWidth` | `140` | — | No token |
| `ChatItemSkeleton` | `app/(main)/chat/sidebar/chat-section-element.tsx` | 293 | `height` | `16` (unitless px) | `--space-4` | Exact |
| `MoreChatsSidebar` (sentinel) | `app/(main)/chat/sidebar/more-chats-sidebar.tsx` | 208 | `padding` | `'8px 0'` | `var(--space-2) 0` | Exact (vertical) |
| `MoreChatsSidebar` (loader) | `app/(main)/chat/sidebar/more-chats-sidebar.tsx` | 210 | `padding` | `'4px 0'` | `var(--space-1) 0` | Exact (vertical) |
| `AgentMoreChatsSidebar` (sentinel) | `app/(main)/chat/sidebar/agent-more-chats-sidebar.tsx` | 265 | `padding` | `'8px 0'` | `var(--space-2) 0` | Exact (vertical) |
| `AgentMoreChatsSidebar` (loader) | `app/(main)/chat/sidebar/agent-more-chats-sidebar.tsx` | 267 | `padding` | `'4px 0'` | `var(--space-1) 0` | Exact (vertical) |
| `AgentsSidebar` (sentinel) | `app/(main)/chat/sidebar/agents-sidebar.tsx` | 251 | `padding` | `'8px 0'` | `var(--space-2) 0` | Exact (vertical) |
| `AgentsSidebar` (loader) | `app/(main)/chat/sidebar/agents-sidebar.tsx` | 253 | `padding` | `'4px 0'` | `var(--space-1) 0` | Exact (vertical) |
| `KbdBadge` | `app/(main)/chat/components/chat-sidebar.tsx` | 18 | `padding` | `'2px 4px'` | `var(--space-1)` (for 4px, no 2px token) | Closest (+2px delta on vertical) |
| `ChatSidebar` (legacy) | `app/(main)/chat/components/chat-sidebar.tsx` | 182 | `width` | `'233px'` | — | No token |

### Constants File — `app/components/sidebar/constants.ts`

These are numeric constants used as raw pixel values via template literals (e.g., `` `${SIDEBAR_WIDTH}px` ``). They are referenced in `sidebar-base.tsx` and `secondary-panel.tsx` via string interpolation, which means they produce hardcoded px values at runtime.

| Constant | File Path | Line # | CSS Property (usage site) | Current Value | Radix Variable | Match Type |
|----------|-----------|--------|--------------------------|---------------|----------------|------------|
| `SIDEBAR_WIDTH` | `app/components/sidebar/constants.ts` | 18 | `width`, `left`, `min-width` | `233px` | — | No token |
| `HEADER_HEIGHT` | `app/components/sidebar/constants.ts` | 21 | `height` | `56px` | `--space-7` + `--space-7` (≈56px via calc) | Closest (+0px; 56=32+24, not a standard token) |
| `FOOTER_HEIGHT` | `app/components/sidebar/constants.ts` | 24 | `height` | `60px` | — | No token |
| `ELEMENT_HEIGHT` | `app/components/sidebar/constants.ts` | 27 | `height` | `32px` | `--space-8` | Exact |
| `CHAT_ITEM_HEIGHT` | `app/components/sidebar/constants.ts` | 34 | `height` | `36px` | `--space-9` | Closest (−0px; 36=--space-9) |
| `CONTENT_PADDING` | `app/components/sidebar/constants.ts` | 44 | `padding` | `'16px 8px'` | `var(--space-4) var(--space-2)` | Exact |
| `SECTION_HEADER_PADDING` | `app/components/sidebar/constants.ts` | 47 | `padding` | `'4px 8px'` | `var(--space-1) var(--space-2)` | Exact |
| `SECTION_PADDING_TOP` | `app/components/sidebar/constants.ts` | 50 | `padding-top` | `4px` | `--space-1` | Exact |
| `SECTION_PADDING_BOTTOM` | `app/components/sidebar/constants.ts` | 53 | `padding-bottom` | `16px` | `--space-4` | Exact |
| `SECTION_CONTENT_MARGIN_TOP` | `app/components/sidebar/constants.ts` | 56 | `margin-top` | `4px` | `--space-1` | Exact |
| `EMPTY_STATE_PADDING_X` | `app/components/sidebar/constants.ts` | 59 | `padding` (horizontal) | `24px` | `--space-6` | Exact |
| `EMPTY_STATE_PADDING_Y` | `app/components/sidebar/constants.ts` | 62 | `padding` (vertical) | `8px` | `--space-2` | Exact |
| `FEATURED_ITEM_MARGIN_BOTTOM` | `app/components/sidebar/constants.ts` | 65 | `margin-bottom` | `8px` | `--space-2` | Exact |
| `KBD_BADGE_PADDING` | `app/components/sidebar/constants.ts` | 68 | `padding` | `'2px 4px'` | `var(--space-1)` (4px; no 2px token) | Closest (+2px delta on vertical) |
| `TREE_INDENT_PER_LEVEL` | `app/components/sidebar/constants.ts` | 78 | `padding-left` (computed) | `31px` | — | No token |
| `TREE_BASE_PADDING` | `app/components/sidebar/constants.ts` | 81 | `padding-left` | `12px` | `--space-3` | Exact |
| `TREE_LINE_OFFSET` | `app/components/sidebar/constants.ts` | 87 | offset (computed) | `8px` | `--space-2` | Exact |

### Usage Sites in `app/components/sidebar/sidebar-base.tsx`

Constants from `constants.ts` are interpolated as px strings here:

| Location | File Path | Line # | CSS Property | Current Value | Radix Variable | Match Type |
|----------|-----------|--------|-------------|---------------|----------------|------------|
| Mobile header box | `app/components/sidebar/sidebar-base.tsx` | 61 | `height` | `` `${HEADER_HEIGHT}px` `` → `56px` | — | No token |
| Mobile footer box | `app/components/sidebar/sidebar-base.tsx` | 109 | `height` | `` `${FOOTER_HEIGHT}px` `` → `60px` | — | No token |
| Desktop sidebar | `app/components/sidebar/sidebar-base.tsx` | 124 | `width` | `` `${SIDEBAR_WIDTH}px` `` → `233px` | — | No token |
| Desktop header box | `app/components/sidebar/sidebar-base.tsx` | 136 | `height` | `` `${HEADER_HEIGHT}px` `` → `56px` | — | No token |
| Desktop footer box | `app/components/sidebar/sidebar-base.tsx` | 160 | `height` | `` `${FOOTER_HEIGHT}px` `` → `60px` | — | No token |
| Cluster width | `app/components/sidebar/sidebar-base.tsx` | 172–179 | `width`, `min-width` | `` `${SIDEBAR_WIDTH * 2}px` `` → `466px` | — | No token |
| Backdrop left | `app/components/sidebar/sidebar-base.tsx` | 199 | `left` | `` `${sidebarClusterWidth}px` `` | — | No token |
| Secondary panel left | `app/components/sidebar/sidebar-base.tsx` | 211 | `left` | `` `${SIDEBAR_WIDTH}px` `` → `233px` | — | No token |

---

## Summary

### By Match Type

| Match Type | Count |
|------------|-------|
| Exact | 17 |
| Closest | 5 |
| No token | 14 |

### Key Observations

1. **Most ad-hoc spacing already uses `var(--space-*)` tokens** — the codebase is in good shape for inline style props like `padding: 'var(--space-3)'`, `gap="2"`, etc.

2. **Repeated sentinel/loader pattern** (3 files): `padding: '8px 0'` / `padding: '4px 0'` appear identically in `more-chats-sidebar.tsx`, `agent-more-chats-sidebar.tsx`, and `agents-sidebar.tsx`. All three can be replaced with `var(--space-2) 0` / `var(--space-1) 0`.

3. **Meatball button `padding: 2`**: Both `chat-item-menu.tsx:63` and `agent-sidebar-item-menu.tsx:57` use `padding: 2` (2px). No Radix `--space-*` token exists for 2px — this is an intentional sub-token value for tight icon-button padding.

4. **`minWidth: 140`**: Appears in both dropdown content components. This is a component-specific constraint, not a standard spacing value — no Radix equivalent is appropriate.

5. **`SIDEBAR_WIDTH = 233`**: A domain-specific fixed dimension. No Radix token maps to this; it should remain a named constant.

6. **`HEADER_HEIGHT = 56` / `FOOTER_HEIGHT = 60`**: Composite dimensions (56 = 32 + 24; 60 = 36 + 24). No single Radix token exists for these — they could be expressed as `calc(var(--space-8) + var(--space-6))` / `calc(var(--space-9) + var(--space-6))` but named constants are clearer.

7. **`CHAT_ITEM_HEIGHT = 36`**: Maps exactly to `--space-9` (36px). This constant could reference the token directly.

8. **`ELEMENT_HEIGHT = 32`**: Maps exactly to `--space-8` (32px). Same as above.

9. **`gap: 8` in `SidebarItem`**: Should use the Radix `gap` prop (`gap="2"`) instead of inline `style={{ gap: 8 }}` to be consistent with other Radix components in the file.

10. **`KBD_BADGE_PADDING = '2px 4px'`**: The 4px horizontal component maps to `--space-1`, but the 2px vertical has no Radix equivalent. A common approach is to keep this as a hardcoded value or define a custom CSS variable.

---

## Files Audited (No Findings)

The following sidebar files had **no hardcoded px spacing/sizing values** — they use Radix tokens or constants throughout:

- `app/(main)/chat/sidebar/index.tsx`
- `app/(main)/chat/sidebar/header.tsx`
- `app/(main)/chat/sidebar/chat-section-header.tsx`
- `app/(main)/chat/sidebar/chat-section.tsx`
- `app/(main)/chat/sidebar/chat-sections.tsx`
- `app/(main)/chat/sidebar/static-nav-section.tsx`
- `app/(main)/chat/sidebar/static-nav-section-element.tsx`
- `app/(main)/chat/sidebar/time-group.tsx`
- `app/(main)/chat/sidebar/agent-scoped-chat-sidebar.tsx`
- `app/(main)/chat/sidebar/agent-sidebar-list-row.tsx`
- `app/(main)/chat/sidebar/my-agents-section.tsx`
- `app/(main)/chat/sidebar/chat-section-element.tsx` (except line 293 — skeleton height)
- `app/(main)/chat/sidebar/dialogs/` (all 3 dialog files — no spacing/sizing px values)
- `app/components/sidebar/sidebar-back-header.tsx`
- `app/components/sidebar/secondary-panel.tsx` (uses `CONTENT_PADDING` constant)
- `app/components/sidebar/types.ts`
- `app/components/sidebar/index.ts`
