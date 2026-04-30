export const PANEL_WIDTH_LS_KEY = 'ph.filePreview.panelWidthPx';
export const CITATIONS_WIDTH_LS_KEY = 'ph.filePreview.citationsWidthPx';
/** Fired on `window` when the citations column width is saved (same tab). Keeps multiple hook instances in sync. */
export const CITATIONS_WIDTH_BROADCAST = 'ph-filePreview-citations-width' as const;

export const PANEL_MIN_PX = 360;
export const PANEL_MAX_PX = 1680;
export const DEFAULT_PANEL_NO_CITATIONS_PX = 600; // static fallback for SSR
export const DEFAULT_PANEL_WITH_CITATIONS_PX = 860; // static fallback for SSR

function defaultPanelPx(hasCitations: boolean): number {
  if (typeof window === 'undefined') {
    return hasCitations ? DEFAULT_PANEL_WITH_CITATIONS_PX : DEFAULT_PANEL_NO_CITATIONS_PX;
  }
  const base = Math.round(window.innerWidth * 0.58); // 50 vw
  const withCitations = Math.round(window.innerWidth * 0.75);
  return clamp(
    hasCitations ? withCitations : base,
    PANEL_MIN_PX,
    Math.min(PANEL_MAX_PX, viewportMaxPanelPx()),
  );
}

export const CITATIONS_MIN_PX = 200;
export const CITATIONS_MAX_PX = 520;
export const DEFAULT_CITATIONS_PX = 260;

export function clamp(n: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, n));
}

export function viewportMaxPanelPx(): number {
  if (typeof window === 'undefined') return PANEL_MAX_PX;
  return Math.max(PANEL_MIN_PX, window.innerWidth - 20);
}

export function readSavedPanelWidthPx(hasCitations: boolean): number {
  const fallback = defaultPanelPx(hasCitations);
  if (typeof window === 'undefined') return fallback;
  try {
    const raw = localStorage.getItem(PANEL_WIDTH_LS_KEY);
    if (raw) {
      const n = Number(raw);
      if (!Number.isFinite(n)) return fallback;
      return clamp(n, PANEL_MIN_PX, Math.min(PANEL_MAX_PX, viewportMaxPanelPx()));
    }
  } catch {
    /* ignore */
  }
  return fallback;
}

export function readSavedCitationsWidthPx(): number {
  if (typeof window === 'undefined') return DEFAULT_CITATIONS_PX;
  try {
    const raw = localStorage.getItem(CITATIONS_WIDTH_LS_KEY);
    if (raw) {
      const n = Number(raw);
      if (!Number.isFinite(n)) return DEFAULT_CITATIONS_PX;
      return clamp(n, CITATIONS_MIN_PX, CITATIONS_MAX_PX);
    }
  } catch {
    /* ignore */
  }
  return DEFAULT_CITATIONS_PX;
}
