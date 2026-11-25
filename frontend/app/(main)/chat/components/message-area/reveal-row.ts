/**
 * Where to scroll so a built-in's result row can be read: the part of the scroller above the composer, which is
 * sticky inside it and covers the end of the transcript. Coordinates are the viewport's (getBoundingClientRect);
 * null when the row is already wholly in that part.
 */
export function revealScrollTop(m: {
  rowTop: number;
  rowBottom: number;
  viewTop: number;
  viewBottom: number;
  scrollTop: number;
  padding: number;
}): number | null {
  if (m.rowTop >= m.viewTop && m.rowBottom <= m.viewBottom) return null;
  // A row taller than the space, or above it, is read from its start (a /help list, a /files tree).
  const showStart = m.rowTop < m.viewTop || m.rowBottom - m.rowTop > m.viewBottom - m.viewTop - 2 * m.padding;
  const next = showStart
    ? m.scrollTop + (m.rowTop - m.viewTop) - m.padding
    : m.scrollTop + (m.rowBottom - m.viewBottom) + m.padding;
  return Math.max(0, next);
}
