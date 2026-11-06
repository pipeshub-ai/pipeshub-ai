/** The right column's card width, hairline excluded: what the reader set by dragging, kept in this browser. */
export const COLUMN_DEFAULT_PX = 360;
/** Fits the four tabs. */
export const COLUMN_MIN_PX = 352;
export const COLUMN_MAX_PX = 720;
const COLUMN_LS_KEY = 'chat-right-column-width-px';

/** Within 352 and the smaller of 720 and half the window. (The chat keeps its own minimum in the layout.) */
export function clampColumnWidth(px: number, viewport: number): number {
  return Math.round(Math.max(COLUMN_MIN_PX, Math.min(px, COLUMN_MAX_PX, viewport / 2)));
}

export function storedColumnWidth(): number {
  if (typeof window === 'undefined') return COLUMN_DEFAULT_PX;
  try {
    const saved = parseInt(localStorage.getItem(COLUMN_LS_KEY) ?? '', 10);
    return Number.isFinite(saved) ? saved : COLUMN_DEFAULT_PX;
  } catch {
    return COLUMN_DEFAULT_PX;
  }
}

export function storeColumnWidth(px: number): void {
  try {
    localStorage.setItem(COLUMN_LS_KEY, String(px));
  } catch {
    // Without storage the width holds until the page reloads.
  }
}
