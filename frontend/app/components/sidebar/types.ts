import type { ReactNode } from 'react';

/**
 * Props for the SidebarBase layout component.
 *
 * header and footer are optional — Chat uses both, KB uses neither.
 */
export interface SidebarBaseProps {
  /** Optional fixed-height header at the top of the sidebar */
  header?: ReactNode;
  /** Main scrollable content area */
  children: ReactNode;
  /** Optional fixed-height footer at the bottom of the sidebar */
  footer?: ReactNode;
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
