/**
 * Sidebar Constants
 * 
 * Centralized constants for consistent styling and behavior across the sidebar.
 * These constants ensure maintainability and easy configuration.
 * 
 * @module sidebar.constants
 */

/**
 * Spacing constants for consistent padding and margins
 */
export const SPACING = {
  /** Extra small spacing */
  XS: 0.25,
  /** Small spacing */
  SM: 0.5,
  /** Medium spacing */
  MD: 1,
  /** Large spacing */
  LG: 1.5,
  /** Extra large spacing */
  XL: 2,
  /** Double extra large spacing */
  XXL: 2.5,
} as const;

/**
 * Icon size constants in pixels
 */
export const ICON_SIZES = {
  /** Extra small icon (12px) */
  XS: 12,
  /** Small icon (14px) */
  SM: 14,
  /** Medium icon (16px) */
  MD: 16,
  /** Large icon (18px) */
  LG: 18,
  /** Extra large icon (20px) */
  XL: 20,
  /** Double extra large icon (24px) */
  XXL: 24,
} as const;

/**
 * Font size constants in rem
 */
export const FONT_SIZES = {
  /** Extra small (0.65rem) */
  XS: '0.65rem',
  /** Small (0.7rem) */
  SM: '0.7rem',
  /** Regular (0.75rem) */
  REGULAR: '0.75rem',
  /** Medium (0.8rem) */
  MD: '0.8rem',
  /** Medium-Large (0.85rem) */
  ML: '0.85rem',
  /** Large (0.875rem) */
  LG: '0.875rem',
  /** Extra large (0.9rem) */
  XL: '0.9rem',
  /** Heading (1rem) */
  HEADING: '1rem',
} as const;

/**
 * Border radius constants
 */
export const BORDER_RADIUS = {
  /** Small radius */
  SM: 1,
  /** Medium radius */
  MD: 1.5,
  /** Large radius */
  LG: 2,
} as const;

/**
 * Opacity constants for consistent transparency
 */
export const OPACITY = {
  /** Disabled/subtle (0.05) */
  DISABLED: 0.05,
  /** Very light (0.1) */
  VERY_LIGHT: 0.1,
  /** Light (0.2) */
  LIGHT: 0.2,
  /** Medium (0.4) */
  MEDIUM: 0.4,
  /** Semi-transparent (0.6) */
  SEMI: 0.6,
  /** Almost opaque (0.7) */
  HIGH: 0.7,
  /** Nearly full (0.8) */
  VERY_HIGH: 0.8,
} as const;

/**
 * Padding levels for list items
 */
export const PADDING_LEVELS = {
  /** Base padding from left edge */
  BASE: 4,
  /** Sub-item indentation */
  SUB_ITEM: 5.5,
  /** Nested category */
  NESTED: 2,
} as const;

/**
 * Default icon paths
 */
export const DEFAULT_ICONS = {
  /** Default connector icon */
  CONNECTOR: '/assets/icons/connectors/collections-gray.svg',
  /** Default model icon */
  MODEL: '/assets/icons/models/default.svg',
  /** Default generic icon */
  GENERIC: '/assets/icons/default.svg',
} as const;

/**
 * Animation durations in milliseconds
 */
export const ANIMATION_DURATION = {
  /** Fast transition (200ms) */
  FAST: 200,
  /** Normal transition (300ms) */
  NORMAL: 300,
  /** Slow transition (500ms) */
  SLOW: 500,
} as const;

/**
 * Z-index layering
 */
export const Z_INDEX = {
  /** Base layer */
  BASE: 1,
  /** Elevated layer */
  ELEVATED: 10,
  /** Overlay layer */
  OVERLAY: 100,
  /** Modal layer */
  MODAL: 1000,
} as const;

/**
 * Scrollbar styling constants
 */
export const SCROLLBAR = {
  /** Width of scrollbar */
  WIDTH: '4px',
  /** Border radius of scrollbar thumb */
  BORDER_RADIUS: '8px',
} as const;

/**
 * Category initial expansion state
 */
export const DEFAULT_CATEGORY_EXPANSION = {
  'Input / Output': true,
  'Agents': false,
  'LLM Models': false,
  'Knowledge': false,
  'Tools': true,
  'Vector Stores': false,
} as const;

/**
 * Sidebar dimensions
 */
export const SIDEBAR_DIMENSIONS = {
  /** Header height offset for content area */
  HEADER_OFFSET: '140px',
} as const;

/**
 * Item count display constants
 */
export const ITEM_COUNT = {
  /** Minimum items to show in collapsed view */
  MIN_DISPLAY: 2,
  /** Maximum items to show before "+N more" */
  MAX_DISPLAY: 3,
} as const;

/**
 * Drag and drop constants
 */
export const DRAG_DROP = {
  /** Primary data transfer type */
  DATA_TYPE: 'application/reactflow',
  /** Cursor style when grabbing */
  CURSOR_GRAB: 'grab',
  /** Cursor style when dragging */
  CURSOR_GRABBING: 'grabbing',
} as const;

/**
 * Connector configuration route
 */
export const ROUTES = {
  /** Route to connector configuration page */
  CONNECTOR_CONFIG: '/dashboard/connectors',
} as const;

/**
 * Search placeholder text
 */
export const PLACEHOLDERS = {
  /** Search input placeholder */
  SEARCH: 'Search',
} as const;

/**
 * Empty state messages
 */
export const EMPTY_MESSAGES = {
  /** No tools available message */
  NO_TOOLS: 'No tools available.',
  /** No components available message */
  NO_COMPONENTS: 'No components available',
  /** No tools connected message */
  NO_TOOLS_CONNECTED: 'No tools connected',
} as const;

/**
 * Accessibility labels
 */
export const ARIA_LABELS = {
  /** Search input */
  SEARCH_INPUT: 'Search components',
  /** Clear search button */
  CLEAR_SEARCH: 'Clear search',
  /** Expand/collapse category */
  TOGGLE_CATEGORY: 'Toggle category',
  /** Configure connector */
  CONFIGURE_CONNECTOR: 'Configure connector',
  /** Drag to add node */
  DRAG_NODE: 'Drag to add to canvas',
} as const;

/**
 * Transition configurations
 */
export const TRANSITIONS = {
  /** All properties with ease timing */
  ALL_EASE: 'all 0.2s ease',
  /** Transform with cubic-bezier */
  TRANSFORM: 'all 0.3s cubic-bezier(0.4, 0, 0.2, 1)',
} as const;

