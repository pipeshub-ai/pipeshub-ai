import type { ReactNode } from 'react';

/**
 * Props for the SidebarBase layout component.
 *
 * header and footer are optional — Chat uses both, KB uses neither.
 */
export interface SidebarBaseProps {
  /** Optional fixed-height header at the top of the sidebar */
  header?: ReactNode;
  /** Optional block under the header that stays put while the content below it scrolls */
  pinned?: ReactNode;
  /** Main scrollable content area */
  children: ReactNode;
  /**
   * What the scroll area lists, for a sidebar that swaps it in place (the chat rail's main, agent and project views).
   * Another key starts the scroll area as a new element: a different list, not the same one moved. A list shown again
   * under its key opens where it was left.
   */
  contentKey?: string;
  /** Optional fixed-height footer at the bottom of the sidebar */
  footer?: ReactNode;
  /** Footer height in px, or 'auto' to fit its content. Defaults to FOOTER_HEIGHT. */
  footerHeight?: number | 'auto';
  /**
   * Optional secondary panel that slides in to the right of the primary sidebar.
   * Used for "More Chats", "More items", etc.
   * When provided, both panels are rendered side-by-side.
   */
  secondaryPanel?: ReactNode;
  /**
   * Called when the user clicks outside the secondary panel (on the backdrop).
   * Only relevant when `secondaryPanel` is provided.
   * This is a root-level concern — the SidebarBase renders the
   * backdrop overlay, so pages don't need their own click-outside logic.
   */
  onDismissSecondaryPanel?: () => void;
  // ── Mobile drawer props ─────────────────────────────
  /** When true the sidebar renders as a fixed full-width overlay instead of an inline panel */
  isMobile?: boolean;
  /** Whether the mobile drawer is currently open */
  mobileOpen?: boolean;
  /** Called when the mobile drawer should close (× button or backdrop tap) */
  onMobileClose?: () => void;
}

/**
 * Props for the SecondaryPanel component rendered inside SidebarBase.
 */
export interface SecondaryPanelProps {
  /** Fixed-height header (typically SidebarBackHeader) */
  header: ReactNode;
  /** Scrollable content area */
  children: ReactNode;
}
