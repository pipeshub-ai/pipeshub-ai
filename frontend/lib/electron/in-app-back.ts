import { isElectron } from './is-electron';

/**
 * Whether a standalone page (e.g. /record/<id>) should offer its own Back
 * button. The web UI opens these pages in a new tab, but the desktop app has a
 * single window without browser chrome, so once it navigates there the page is
 * the only way back to where the user came from.
 */
export function canGoBackInApp(): boolean {
  return isElectron() && typeof window !== 'undefined' && window.history.length > 1;
}
