import type { NotificationListItem } from './api';

/** How a row shows one notification; absent fields keep the defaults. */
export interface NotificationRowDecoration {
  title?: string;
  message?: string;
  /** `null`: the row has nothing to open. Absent: the notification's own link. */
  href?: string | null;
  icon?: string;
  iconColor?: string;
  /** A second line under the title. */
  subtitle?: string;
}

/** One more action button on a row. */
export interface NotificationRowExtraAction {
  label: string;
  icon: string;
  onClick(): void;
}

export interface NotificationPanelRowsOptions {
  isPanelOpen: boolean;
  /** `false` when the row already has an action running. */
  beginRowAction(id: string): boolean;
  endRowAction(id: string): void;
  setError(message: string | null): void;
}

export interface NotificationPanelRows {
  decorate(n: NotificationListItem): NotificationRowDecoration | null;
  extraAction(n: NotificationListItem): NotificationRowExtraAction | null;
}

export interface NotificationSocketOptions {
  isHydrated: boolean;
  isAuthenticated: boolean;
  accessToken: string | null | undefined;
}

export interface NotificationExtension {
  usePanelRows(opts: NotificationPanelRowsOptions): NotificationPanelRows;
  useSocketEffects(opts: NotificationSocketOptions): void;
  /** The notification socket (re)connected. */
  onSocketConnect(): void;
}

const NO_PANEL_ROWS: NotificationPanelRows = { decorate: () => null, extraAction: () => null };

export const defaultNotificationExtension: NotificationExtension = {
  usePanelRows: () => NO_PANEL_ROWS,
  useSocketEffects: () => {},
  onSocketConnect: () => {},
};
