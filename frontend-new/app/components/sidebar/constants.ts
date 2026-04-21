/**
 * Sidebar layout constants
 *
 * Import these in page-specific sidebar implementations
 * to ensure consistent sizing across all navigation sidebars.
 *
 * USAGE:
 * - Use Radix space tokens (gap="2" = 8px) for flex gaps instead of pixel values
 * - Override section header height via optional `height` prop when needed
 * - All spacing values designed for 32px base rhythm
 */

// ============================================
// CORE DIMENSIONS
// ============================================

/** Fixed sidebar width in pixels - applies to all navigation sidebars */
export const SIDEBAR_WIDTH = 233;

/** Minimum sidebar width when resizing (same as default) */
export const SIDEBAR_MIN_WIDTH = 233;

/** Maximum sidebar width when resizing */
export const SIDEBAR_MAX_WIDTH = 450;

/** Height of the optional sidebar header area (e.g., logo + avatar in Chat) */
export const HEADER_HEIGHT = 56;

/** Height of the optional sidebar footer area (e.g., org selector in Chat) */
export const FOOTER_HEIGHT = 60;

/** Height of a single sidebar element/item row (nav items, time-group labels) */
export const ELEMENT_HEIGHT = 32;

/**
 * Height for chat conversation items in the sidebar.
 * Distinct from ELEMENT_HEIGHT (32px) because chat items need
 * extra vertical space for readability.
 */
export const CHAT_ITEM_HEIGHT = 36;

/** Height of large header elements (logo, avatar) in header/footer slots */
export const HEADER_ELEMENT_SIZE = 24;

// ============================================
// SPACING & PADDING
// ============================================

/** Padding for the scrollable content area */
export const CONTENT_PADDING = '16px 8px';

/** Padding for section headers (collapsible sections) */
export const SECTION_HEADER_PADDING = '4px 8px';

/** Top padding for a sidebar section */
export const SECTION_PADDING_TOP = 4;

/** Bottom padding for a sidebar section */
export const SECTION_PADDING_BOTTOM = 16;

/** Margin top for content after section headers */
export const SECTION_CONTENT_MARGIN_TOP = 4;

/** Horizontal padding for empty state messages */
export const EMPTY_STATE_PADDING_X = 24;

/** Vertical padding for empty state messages */
export const EMPTY_STATE_PADDING_Y = 8;

/** Bottom margin for featured items (e.g., "All Records" button) */
export const FEATURED_ITEM_MARGIN_BOTTOM = 8;

/** Padding for keyboard shortcut badges */
export const KBD_BADGE_PADDING = '2px 4px';

/** Margin bottom for section headers in KB sidebar */
export const KB_SECTION_HEADER_MARGIN_BOTTOM = '4px';

// ============================================
// TREE STRUCTURE (KNOWLEDGE BASE)
// ============================================

/** Indentation per tree nesting level in pixels (used by KB folder trees) */
export const TREE_INDENT_PER_LEVEL = 31;

/** Base left padding for tree items in pixels */
export const TREE_BASE_PADDING = 12;

/**
 * Horizontal offset for tree lines (distance from padding edge to line center)
 * This compensates for icon centering within the toggle button area
 */
export const TREE_LINE_OFFSET = 8;

// ============================================
// ICON SIZES
// ============================================

/** Small icons - indicators, decorative elements */
export const ICON_SIZE_SMALL = 14;

/** Default icons - standard UI elements (most common) */
export const ICON_SIZE_DEFAULT = 16;

/** Large icons - header elements, avatars */
export const ICON_SIZE_LARGE = 24;

// ============================================
// VISUAL STYLES
// ============================================

/** Border style for active/focused elements */
export const ELEMENT_BORDER = '1px solid var(--slate-3)';

/** Background color for hover states */
export const HOVER_BACKGROUND = 'var(--slate-3)';

// ============================================
// COLLECTION LIMITS
// ============================================

/** Maximum number of collections shown in sidebar before "More" button appears */
export const SIDEBAR_COLLECTION_LIMIT = 5;
