/**
 * Hide sidebar and Show sidebar replace each other, so the one that was used disappears
 * with the click. Focus follows to the other one, never to the page body.
 */
export type SidebarToggle = 'show' | 'hide';

function findVisibleToggle(which: SidebarToggle): HTMLElement | null {
  const toggles = document.querySelectorAll<HTMLElement>(`[data-sidebar-toggle="${which}"]`);
  return Array.from(toggles).find((el) => el.getClientRects().length > 0) ?? null;
}

/** Frames to wait for the other control to mount before giving up. */
const MAX_FRAMES = 5;

/** Once the other control has rendered, focuses the visible Show or Hide sidebar control. */
export function focusSidebarToggle(which: SidebarToggle): void {
  if (typeof window === 'undefined') return;
  let frames = 0;
  const attempt = () => {
    const target = findVisibleToggle(which);
    if (target) {
      target.focus();
      return;
    }
    frames += 1;
    if (frames < MAX_FRAMES) {
      window.requestAnimationFrame(attempt);
      return;
    }
    // A rail without its own Hide control (an agent's sidebar): its first control instead.
    if (which === 'hide') {
      document
        .querySelector<HTMLElement>(
          '[data-ph-sidebar-slot] button, [data-ph-sidebar-slot] [href], [data-ph-sidebar-slot] [tabindex="0"]',
        )
        ?.focus();
    }
  };
  window.requestAnimationFrame(attempt);
}
