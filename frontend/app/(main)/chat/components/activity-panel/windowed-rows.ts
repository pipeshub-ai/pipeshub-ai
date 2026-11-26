/** Rows rendered beyond each edge of the viewport, so fast scrolling does not show blank space. */
const OVERSCAN_ROWS = 40;

export interface RowWindow {
  start: number;
  end: number;
  padTop: number;
  padBottom: number;
}

/**
 * The rows of a fixed-row-height list that a viewport shows (FE-5): only
 * these are rendered, the rest is padding, so a 10,000-line output costs a
 * few dozen DOM rows.
 */
export function windowRows(
  count: number,
  rowHeight: number,
  viewportHeight: number,
  scrollTop: number,
  overscan: number = OVERSCAN_ROWS,
): RowWindow {
  const visible = Math.max(1, Math.ceil(viewportHeight / rowHeight));
  const maxTop = Math.max(0, count * rowHeight - viewportHeight);
  const top = Math.min(Math.max(0, scrollTop), maxTop);
  const first = Math.floor(top / rowHeight);
  const start = Math.max(0, first - overscan);
  const end = Math.min(count, first + visible + overscan);
  return { start, end, padTop: start * rowHeight, padBottom: (count - end) * rowHeight };
}
