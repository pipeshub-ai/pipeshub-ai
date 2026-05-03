# Radix Spacing Token Audit — `/chat` Components

Audit of hardcoded `px` values in the `/app/(main)/chat` directory that should be replaced with Radix UI `var(--space-*)` and related token variables.

## Radix Token Reference

| px value | `--space-*` | `--font-size-*` | `--line-height-*` | `--radius-*` |
|----------|-------------|-----------------|-------------------|--------------|
| 4px | `--space-1` | — | — | `--radius-2` |
| 6px | — | — | — | `--radius-3` (~6px) |
| 8px | `--space-2` | — | — | — |
| 12px | `--space-3` | `--font-size-1` | `--line-height-1` (16px) | — |
| 14px | — | `--font-size-2` | — | — |
| 16px | `--space-4` | `--font-size-3` | `--line-height-1` | — |
| 20px | — | — | `--line-height-2` | — |
| 24px | `--space-5` | — | `--line-height-3` | — |
| 32px | `--space-6` | — | — | — |
| 40px | `--space-7` | — | — | — |
| 48px | `--space-8` | — | — | — |
| 3px | — | — | — | `--radius-1` |

> **Match legend:** "Exact" = direct token match | "Closest" = nearest token (value differs slightly) | "Leave" = no suitable token, keep hardcoded

---

## Sidebar Components

| Component | File | Line | Property | Current Value | Radix Token | Match |
|-----------|------|------|----------|---------------|-------------|-------|
| `chat-section-element` | `app/(main)/chat/sidebar/chat-section-element.tsx` | 143 | `padding` | `'0 12px'` | `var(--space-3)` | Exact |
| `chat-section-element` | `app/(main)/chat/sidebar/chat-section-element.tsx` | 164 | `lineHeight` | `'20px'` | `var(--line-height-2)` | Exact |
| `time-group` | `app/(main)/chat/sidebar/time-group.tsx` | 85 | `padding` | `'0 12px'` | `var(--space-3)` | Exact |
| `time-group` | `app/(main)/chat/sidebar/time-group.tsx` | 92 | `lineHeight` | `'16px'` | `var(--line-height-1)` | Exact |
| `time-group` | `app/(main)/chat/sidebar/time-group.tsx` | 93 | `letterSpacing` | `'0.04px'` | — | Leave (Radix uses em-based tokens) |
| `more-chats-sidebar` | `app/(main)/chat/sidebar/more-chats-sidebar.tsx` | 167 | `lineHeight` | `'16px'` | `var(--line-height-1)` | Exact |
| `more-chats-sidebar` | `app/(main)/chat/sidebar/more-chats-sidebar.tsx` | 168 | `letterSpacing` | `'0.04px'` | — | Leave |
| `more-chats-sidebar` | `app/(main)/chat/sidebar/more-chats-sidebar.tsx` | 198 | `padding` | `'16px 12px'` | `var(--space-4) var(--space-3)` | Exact |
| `agents-sidebar` | `app/(main)/chat/sidebar/agents-sidebar.tsx` | 213 | `padding` | `'24px 12px'` | `var(--space-5) var(--space-3)` | Exact |
| `agents-sidebar` | `app/(main)/chat/sidebar/agents-sidebar.tsx` | 217 | `padding` | `'16px 12px'` | `var(--space-4) var(--space-3)` | Exact |
| `agents-sidebar` | `app/(main)/chat/sidebar/agents-sidebar.tsx` | 261 | `padding` | `'16px 12px'` | `var(--space-4) var(--space-3)` | Exact |
| `agent-more-chats-sidebar` | `app/(main)/chat/sidebar/agent-more-chats-sidebar.tsx` | 257 | `padding` | `'16px 12px'` | `var(--space-4) var(--space-3)` | Exact |
| `sidebar-item` | `app/(main)/chat/sidebar/sidebar-item.tsx` | 82 | `padding` | `'0 12px'` | `var(--space-3)` | Exact |
| `sidebar-item` | `app/(main)/chat/sidebar/sidebar-item.tsx` | 100 | `lineHeight` | `'20px'` | `var(--line-height-2)` | Exact |
| `sidebar-item` | `app/(main)/chat/sidebar/sidebar-item.tsx` | 116 | `lineHeight` | `'20px'` | `var(--line-height-2)` | Exact |
| `static-nav-section-element` | `app/(main)/chat/sidebar/static-nav-section-element.tsx` | 46 | `lineHeight` | `'20px'` | `var(--line-height-2)` | Exact |
| `chat-section-header` | `app/(main)/chat/sidebar/chat-section-header.tsx` | 28 | `padding` | `'0 12px'` | `var(--space-3)` | Exact |
| `chat-section-header` | `app/(main)/chat/sidebar/chat-section-header.tsx` | 36 | `lineHeight` | `'16px'` | `var(--line-height-1)` | Exact |
| `chat-section-header` | `app/(main)/chat/sidebar/chat-section-header.tsx` | 37 | `letterSpacing` | `'0.04px'` | — | Leave |
| `static-nav-section` | `app/(main)/chat/sidebar/static-nav-section.tsx` | 46 | `lineHeight` | `'16px'` | `var(--line-height-1)` | Exact |
| `static-nav-section` | `app/(main)/chat/sidebar/static-nav-section.tsx` | 47 | `letterSpacing` | `'0.04px'` | — | Leave |

---

## Components — General & Search

| Component | File | Line | Property | Current Value | Radix Token | Match |
|-----------|------|------|----------|---------------|-------------|-------|
| `agent-strategy-dropdown` | `app/(main)/chat/components/agent-strategy-dropdown.tsx` | 74 | `minWidth`/`maxWidth` | `min(320px, calc(100vw - 32px))` / `360px` | `var(--space-6)` for `32px` calc offset | Closest |
| `agent-strategy-dropdown` | `app/(main)/chat/components/agent-strategy-dropdown.tsx` | 96 | `marginTop` | `'1px'` | — | Leave (fine-tune offset) |
| `agent-chat-header` | `app/(main)/chat/components/agent-chat-header.tsx` | 68 | `padding` | `'6px 10px'` | `var(--space-1) var(--space-2)` (4px 8px) | Closest |
| `agent-chat-header` | `app/(main)/chat/components/agent-chat-header.tsx` | 81 | `lineHeight` | `'22px'` | `var(--line-height-3)` (24px) | Closest |
| `search-result-card` | `app/(main)/chat/components/search-results/search-result-card.tsx` | 79, 116, 150 | `height` | `'24px'` | — | Leave (icon height) |
| `search-results-view` | `app/(main)/chat/components/search-results/search-results-view.tsx` | 153 | `height` | `'40px'` | `var(--space-7)` | Exact |
| `search-results-view` | `app/(main)/chat/components/search-results/search-results-view.tsx` | 179 | `height` | `'2px'` | — | Leave (divider) |
| `search-results-view` | `app/(main)/chat/components/search-results/search-results-view.tsx` | 203 | `height` | `'1px'` | — | Leave (divider) |
| `search-results-view` | `app/(main)/chat/components/search-results/search-results-view.tsx` | 220 | `paddingBottom` | `'220px'` | — | Leave (layout-specific) |

---

## Message Area

| Component | File | Line | Property | Current Value | Radix Token | Match |
|-----------|------|------|----------|---------------|-------------|-------|
| `message-list` | `app/(main)/chat/components/message-area/message-list.tsx` | 268, 277 | `minHeight` | `'0px'` | `0` | Replace with bare `0` |
| `message-list` | `app/(main)/chat/components/message-area/message-list.tsx` | 848 | `paddingTop` | `'16px'` | `var(--space-4)` | Exact |
| `message-list` | `app/(main)/chat/components/message-area/message-list.tsx` | 849 | `paddingBottom` | `isMobile ? '40px' : '100px'` | `var(--space-7)` for 40px; leave 100px | Closest |
| `ask-more` | `app/(main)/chat/components/message-area/ask-more.tsx` | 25 | `height` | `'1px'` | — | Leave (divider) |
| `ask-more` | `app/(main)/chat/components/message-area/ask-more.tsx` | 62 | `padding` | `'8px 8px 8px 12px'` | `var(--space-2) var(--space-2) var(--space-2) var(--space-3)` | Exact |
| `ask-more` | `app/(main)/chat/components/message-area/ask-more.tsx` | 88 | `marginLeft` | `'8px'` | `var(--space-2)` | Exact |
| `confidence-indicator` | `app/(main)/chat/components/message-area/confidence-indicator.tsx` | 47 | `fontSize` | `'11px'` | `var(--font-size-1)` (12px) | Closest |
| `answer-content` | `app/(main)/chat/components/message-area/answer-content.tsx` | 151 | `lineHeight` | `'20px'` | `var(--line-height-2)` | Exact |
| `answer-content` | `app/(main)/chat/components/message-area/answer-content.tsx` | 167 | `padding` | `'2px 6px'` | `var(--space-1)` for 6px side | Closest |
| `answer-content` | `app/(main)/chat/components/message-area/answer-content.tsx` | 170, 199, 214, 236 | `fontSize` | `'14px'` | `var(--font-size-2)` | Exact |
| `answer-content` | `app/(main)/chat/components/message-area/answer-content.tsx` | 184 | `maxHeight` | `'400px'` | — | Leave (layout-specific) |
| `message-actions` | `app/(main)/chat/components/message-area/message-actions.tsx` | 372, 397 | `height` | `'24px'` | — | Leave (icon/button size) |
| `message-actions` | `app/(main)/chat/components/message-area/message-actions.tsx` | 381, 411 | `lineHeight` | `'16px'` | `var(--line-height-1)` | Exact |
| `message-sources` | `app/(main)/chat/components/message-area/message-sources.tsx` | 78 | `padding` | `'2px 6px'` | `var(--space-1)` for 6px side | Closest |

---

## Citations

| Component | File | Line | Property | Current Value | Radix Token | Match |
|-----------|------|------|----------|---------------|-------------|-------|
| `inline-citation-badge` | `app/(main)/chat/components/message-area/response-tabs/citations/inline-citation-badge.tsx` | 46, 78 | `padding` | `'2px 6px'` | `var(--space-1)` for 6px side | Closest |
| `inline-citation-badge` | `app/(main)/chat/components/message-area/response-tabs/citations/inline-citation-badge.tsx` | 49, 82 | `marginLeft` | `'4px'` | `var(--space-1)` | Exact |
| `inline-citation-badge` | `app/(main)/chat/components/message-area/response-tabs/citations/inline-citation-badge.tsx` | 50, 83 | `marginRight` | `'2px'` | — | Leave (fine-tune) |
| `inline-citation-badge` | `app/(main)/chat/components/message-area/response-tabs/citations/inline-citation-badge.tsx` | 51 | `minWidth` | `'18px'` | — | Leave (badge size) |
| `inline-citation-badge` | `app/(main)/chat/components/message-area/response-tabs/citations/inline-citation-badge.tsx` | 52, 85 | `height` | `'20px'` | `var(--space-5)` (24px) | Closest |
| `inline-citation-badge` | `app/(main)/chat/components/message-area/response-tabs/citations/inline-citation-badge.tsx` | 58, 98 | `fontSize` | `'11px'` | `var(--font-size-1)` (12px) | Closest |
| `inline-citation-badge` | `app/(main)/chat/components/message-area/response-tabs/citations/inline-citation-badge.tsx` | 100 | `maxWidth` | `'300px'` | — | Leave (layout-specific) |
| `inline-citation-badge` | `app/(main)/chat/components/message-area/response-tabs/citations/inline-citation-badge.tsx` | 115 | `borderRadius` | `'2px'` | `var(--radius-1)` (3px) | Closest |
| `inline-citation-badge` | `app/(main)/chat/components/message-area/response-tabs/citations/inline-citation-badge.tsx` | 116 | `padding` | `'0 3px'` | `var(--space-1)` (4px) | Closest |
| `inline-citation-badge` | `app/(main)/chat/components/message-area/response-tabs/citations/inline-citation-badge.tsx` | 117 | `minWidth` | `'14px'` | — | Leave (badge size) |
| `inline-citation-badge` | `app/(main)/chat/components/message-area/response-tabs/citations/inline-citation-badge.tsx` | 118 | `height` | `'14px'` | — | Leave (badge size) |
| `inline-citation-badge` | `app/(main)/chat/components/message-area/response-tabs/citations/inline-citation-badge.tsx` | 128 | `fontSize` | `'10px'` | `var(--font-size-1)` (12px) | Closest |
| `inline-citation-badge` | `app/(main)/chat/components/message-area/response-tabs/citations/inline-citation-badge.tsx` | 149 | `width` | `'420px'` | — | Leave (layout-specific) |
| `citation-popover` | `app/(main)/chat/components/message-area/response-tabs/citations/citation-popover.tsx` | 70, 103 | `height` | `'24px'` | — | Leave (icon height) |
| `citation-card` | `app/(main)/chat/components/message-area/response-tabs/citations/citation-card.tsx` | 91 | `gap` | `'32px'` | `var(--space-6)` | Exact |
| `citation-card` | `app/(main)/chat/components/message-area/response-tabs/citations/citation-card.tsx` | 134, 170 | `height` | `'24px'` | — | Leave (icon height) |
| `citation-card` | `app/(main)/chat/components/message-area/response-tabs/citations/citation-card.tsx` | 331, 359 | `height` | `'28px'` | — | Leave (chip height) |
| `response-tabs` | `app/(main)/chat/components/message-area/response-tabs/response-tabs.tsx` | 36 | `height` | `'40px'` | `var(--space-7)` | Exact |
| `response-tabs` | `app/(main)/chat/components/message-area/response-tabs/response-tabs.tsx` | 64 | `padding` | `'0 6px'` | `var(--space-1)` (4px) | Closest |
| `response-tabs` | `app/(main)/chat/components/message-area/response-tabs/response-tabs.tsx` | 82 | `height` | `'2px'` | — | Leave (indicator line) |

---

## Chat Input & Panel

| Component | File | Line | Property | Current Value | Radix Token | Match |
|-----------|------|------|----------|---------------|-------------|-------|
| `chat-input` | `app/(main)/chat/components/chat-input.tsx` | 553, 604 | `borderRadius` | `'3px'` | `var(--radius-1)` | Exact |
| `chat-input` | `app/(main)/chat/components/chat-input.tsx` | 710 | `minHeight` | `'0px'` | `0` | Replace with bare `0` |
| `chat-input` | `app/(main)/chat/components/chat-input.tsx` | 772 | `minHeight` | `'24px'` | — | Leave (textarea min) |
| `chat-input` | `app/(main)/chat/components/chat-input.tsx` | 773, 806 | `maxHeight` | `'120px'` | — | Leave (layout-specific) |
| `chat-input` | `app/(main)/chat/components/chat-input.tsx` | 805 | `minHeight` | `isMobile ? '36px' : '64px'` | — | Leave (layout-specific) |
| `chat-sidebar` | `app/(main)/chat/components/chat-sidebar.tsx` | 18 | `padding` | `'2px 4px'` | `var(--space-1)` for 4px | Closest |
| `chat-sidebar` | `app/(main)/chat/components/chat-sidebar.tsx` | 130 | `height` | `'16px'` | `var(--space-4)` | Exact |
| `chat-sidebar` | `app/(main)/chat/components/chat-sidebar.tsx` | 192 | `height` | `'40px'` | `var(--space-7)` | Exact |
| `chat-sidebar` | `app/(main)/chat/components/chat-sidebar.tsx` | 196, 316 | `width` | `'24px'` | `var(--space-5)` | Exact |
| `chat-sidebar` | `app/(main)/chat/components/chat-sidebar.tsx` | 197, 317 | `height` | `'24px'` | `var(--space-5)` | Exact |
| `selected-collections` | `app/(main)/chat/components/selected-collections.tsx` | 35 | `borderRadius` | `'3px'` | `var(--radius-1)` | Exact |
| `selected-collections` | `app/(main)/chat/components/selected-collections.tsx` | 49 | `width` | `'16px'` | `var(--space-4)` | Exact |
| `selected-collections` | `app/(main)/chat/components/selected-collections.tsx` | 50 | `height` | `'16px'` | `var(--space-4)` | Exact |
| `selected-collections` | `app/(main)/chat/components/selected-collections.tsx` | 69 | `lineHeight` | `'16px'` | `var(--line-height-1)` | Exact |
| `selected-collections` | `app/(main)/chat/components/selected-collections.tsx` | 144, 145, 177, 178 | `width`/`height` | `'28px'` | — | Leave (icon size) |
| `model-selector-panel` | `app/(main)/chat/components/chat-panel/expansion-panels/model-selector/model-selector-panel.tsx` | 251 | `width` | `'16px'` | `var(--space-4)` | Exact |
| `model-selector-panel` | `app/(main)/chat/components/chat-panel/expansion-panels/model-selector/model-selector-panel.tsx` | 252 | `height` | `'16px'` | `var(--space-4)` | Exact |
| `collection-row` | `app/(main)/chat/components/chat-panel/expansion-panels/connectors-collections/collection-row.tsx` | 38 | `height` | `'40px'` | `var(--space-7)` | Exact |
| `collections-tab` | `app/(main)/chat/components/chat-panel/expansion-panels/connectors-collections/collections-tab.tsx` | 108 | `height` | `'36px'` | `var(--space-7)` (40px) | Closest |
| `collections-tab` | `app/(main)/chat/components/chat-panel/expansion-panels/connectors-collections/collections-tab.tsx` | 132 | `fontSize` | `'13px'` | `var(--font-size-2)` (14px) | Closest |
| `collections-tab` | `app/(main)/chat/components/chat-panel/expansion-panels/connectors-collections/collections-tab.tsx` | 200 | `height` | `'28px'` | — | Leave (chip height) |
| `collections-tab` | `app/(main)/chat/components/chat-panel/expansion-panels/connectors-collections/collections-tab.tsx` | 208 | `lineHeight` | `'16px'` | `var(--line-height-1)` | Exact |
| `collections-tab` | `app/(main)/chat/components/chat-panel/expansion-panels/connectors-collections/collections-tab.tsx` | 209 | `letterSpacing` | `'0.04px'` | — | Leave |
| `query-mode-panel` | `app/(main)/chat/components/chat-panel/expansion-panels/query-mode-panel.tsx` | 150 | `width` | `'16px'` | `var(--space-4)` | Exact |
| `query-mode-panel` | `app/(main)/chat/components/chat-panel/expansion-panels/query-mode-panel.tsx` | 151 | `height` | `'16px'` | `var(--space-4)` | Exact |
| `chat-input-overlay-panel` | `app/(main)/chat/components/chat-panel/expansion-panels/chat-input-overlay-panel.tsx` | 117 | `height` | `'512px'` | — | Leave (layout-specific) |
| `chat-input-overlay-panel` | `app/(main)/chat/components/chat-panel/expansion-panels/chat-input-overlay-panel.tsx` | 148 | `height` | `'44px'` | `var(--space-8)` (48px) | Closest |
| `message-action-indicator` | `app/(main)/chat/components/chat-panel/expansion-panels/message-actions/message-action-indicator.tsx` | 86 | `minHeight` | `'32px'` | `var(--space-6)` | Exact |
| `message-action-indicator` | `app/(main)/chat/components/chat-panel/expansion-panels/message-actions/message-action-indicator.tsx` | 95 | `padding` | `'4px 10px'` | `var(--space-1) var(--space-2)` (4px 8px) | Closest |
| `message-action-indicator` | `app/(main)/chat/components/chat-panel/expansion-panels/message-actions/message-action-indicator.tsx` | 150 | `minHeight` | `'24px'` | — | Leave (textarea min) |
| `message-action-indicator` | `app/(main)/chat/components/chat-panel/expansion-panels/message-actions/message-action-indicator.tsx` | 151 | `maxHeight` | `'120px'` | — | Leave (layout-specific) |
| `mode-switcher` | `app/(main)/chat/components/chat-panel/mode-switcher.tsx` | 73, 74, 93, 127, 184, 185 | `width`/`height` | `'32px'` | `var(--space-6)` | Exact |

---

## Page Level

| Component | File | Line | Property | Current Value | Radix Token | Match |
|-----------|------|------|----------|---------------|-------------|-------|
| `page.tsx` | `app/(main)/chat/page.tsx` | 64 | `height` | `'300px'` | — | Leave (layout-specific) |
| `page.tsx` | `app/(main)/chat/page.tsx` | 749 | `marginTop` | `isMobile ? '36px' : '-44px'` | — | Leave (layout-specific) |
| `page.tsx` | `app/(main)/chat/page.tsx` | 750 | `paddingBottom` | `isMobile ? '140px' : '0'` | — | Leave (layout-specific) |
| `page.tsx` | `app/(main)/chat/page.tsx` | 763 | `marginBottom` | `isMobile ? '32px' : '48px'` | `var(--space-6)` / `var(--space-8)` | Exact |
| `page.tsx` | `app/(main)/chat/page.tsx` | 876 | `marginBottom` | `isMobile ? '120px' : '160px'` | — | Leave (layout-specific) |
| `page.tsx` | `app/(main)/chat/page.tsx` | 877 | `paddingTop` | `isMobile ? '76px' : '56px'` | — | Leave (layout-specific) |
| `page.tsx` | `app/(main)/chat/page.tsx` | 888 | `bottom` | `isMobile ? 0 : '48px'` | `var(--space-8)` | Exact |

---

## Items Left as Hardcoded (intentionally skipped)

| Reason | Examples |
|--------|---------|
| **Divider lines** — semantic, no token | `1px`, `2px` heights |
| **Letter-spacing** — Radix uses em-based tokens, not px | `0.04px` |
| **Layout-specific dimensions** — modal/panel/scroll heights | `512px`, `380px`, `220px`, `140px`, overlay offsets |
| **Fine-tuning offsets** — pixel-perfect alignment | `1px`/`2px` margins |
| **Fixed icon/chip sizes** — design system sizes not yet tokenized | `14px`, `18px`, `20px`, `24px`, `28px` icon dims |
