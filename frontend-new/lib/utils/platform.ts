/**
 * Platform detection utilities.
 *
 * Provides OS-aware modifier key symbols for keyboard shortcut display.
 * - macOS / Linux → ⌘ (Command symbol)
 * - Windows       → ⊞ (Windows key character)
 */

/** Detect whether the current platform is Windows */
export function isWindows(): boolean {
  if (typeof navigator === 'undefined') return false;
  // Modern API first, legacy fallback
  const platform =
    (navigator as Navigator & { userAgentData?: { platform: string } })
      .userAgentData?.platform ?? navigator.platform ?? '';
  return /win/i.test(platform);
}

/** Detect whether the current platform is macOS */
export function isMac(): boolean {
  if (typeof navigator === 'undefined') return false;
  const platform =
    (navigator as Navigator & { userAgentData?: { platform: string } })
      .userAgentData?.platform ?? navigator.platform ?? '';
  return /mac/i.test(platform);
}

/**
 * Returns the modifier key symbol for display:
 * - macOS  → '⌘'
 * - Windows / Linux → 'Ctrl'
 */
export function getModifierSymbol(): string {
  return isMac() ? '⌘' : 'Ctrl';
}

/**
 * Returns true if the "command" key is pressed in an event.
 * On macOS this is metaKey, on Windows/Linux this is ctrlKey.
 */
export function isCommandKey(e: { metaKey: boolean; ctrlKey: boolean }): boolean {
  return isMac() ? e.metaKey : e.ctrlKey;
}
