'use client';

/**
 * Fixed width of the citation popover surface (e.g. opened from
 * `InlineCitationBadge` and `CitationNumberCircle`).
 *
 * Kept as a named constant so the popover stays visually consistent wherever
 * it is rendered, and so the dimension can be tweaked from a single place.
 */
export const CITATION_POPOVER_WIDTH = '420px';

/**
 * Upper bound for the popover width on small viewports, so it never exceeds
 * the visible area.
 */
export const CITATION_POPOVER_MAX_WIDTH = '90vw';
