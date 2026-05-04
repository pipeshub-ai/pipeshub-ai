export interface OrgInfo {
  _id: string;
  registeredName: string;
  shortName: string;
  domain: string;
  accountType: string;
  logoUrl?: string | null;
}

/** Width of the workspace menu popup panel */
export const POPUP_WIDTH = 288;

/** Height of each menu item row */
export const ITEM_HEIGHT = 32;
