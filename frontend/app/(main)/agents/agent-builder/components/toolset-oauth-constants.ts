/** OAuth popup polling / verification — shared by user and agent toolset credential dialogs. */
export const OAUTH_POPUP_POLL_MS = 1000;
export const OAUTH_POPUP_MAX_POLLS = 300;
export const OAUTH_VERIFY_ATTEMPTS = 5;
export const OAUTH_VERIFY_GAP_MS = 1500;
export const OAUTH_POPUP_WIDTH = 600;
export const OAUTH_POPUP_HEIGHT = 700;

export function openCenteredOAuthWindow(url: string, windowName: string): Window | null {
  const w = OAUTH_POPUP_WIDTH;
  const h = OAUTH_POPUP_HEIGHT;
  const left = window.screen.width / 2 - w / 2;
  const top = window.screen.height / 2 - h / 2;
  return window.open(
    url,
    windowName,
    `width=${w},height=${h},left=${left},top=${top},scrollbars=yes,resizable=yes`
  );
}
