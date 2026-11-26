/**
 * The arithmetic of a list whose rows vary in height (EV-15, PF-17): each row's offset from the sizes measured so
 * far, an estimate standing in for a row never rendered, and the rows a viewport over the list shows.
 */

export interface MeasuredWindow {
  /** First row rendered (inclusive). */
  start: number;
  /** Row after the last one rendered (exclusive). */
  end: number;
  /** Height standing in for the rows before `start`. */
  padTop: number;
  /** Height standing in for the rows from `end` on. */
  padBottom: number;
}

/** `offsets[i]` is where row `i` starts, `offsets[count]` the list's height. */
export function rowOffsets(count: number, sizeOf: (index: number) => number): Float64Array {
  const offsets = new Float64Array(count + 1);
  for (let i = 0; i < count; i += 1) offsets[i + 1] = offsets[i] + sizeOf(i);
  return offsets;
}

/** The first row that ends after `y`. */
function firstEndingAfter(offsets: Float64Array, count: number, y: number): number {
  let low = 0;
  let high = count;
  while (low < high) {
    const mid = (low + high) >>> 1;
    if (offsets[mid + 1] <= y) low = mid + 1;
    else high = mid;
  }
  return low;
}

/** The first row that starts at or after `y`. */
function firstStartingAtOrAfter(offsets: Float64Array, count: number, y: number): number {
  let low = 0;
  let high = count;
  while (low < high) {
    const mid = (low + high) >>> 1;
    if (offsets[mid] < y) low = mid + 1;
    else high = mid;
  }
  return low;
}

/**
 * The rows that intersect `[viewTop - overscanPx, viewBottom + overscanPx]`, the view given relative to the list's
 * own top (negative while the list starts below the viewport's top). Always at least one row of a non-empty list,
 * so a list scrolled far past (or a viewport not laid out yet) still has a row to measure from.
 */
export function measuredWindow(
  offsets: Float64Array,
  viewTop: number,
  viewBottom: number,
  overscanPx: number,
): MeasuredWindow {
  const count = offsets.length - 1;
  const total = offsets[count] ?? 0;
  if (count <= 0) return { start: 0, end: 0, padTop: 0, padBottom: 0 };
  const top = Math.max(0, viewTop - overscanPx);
  const bottom = Math.max(top, viewBottom + overscanPx);
  let start = Math.min(firstEndingAfter(offsets, count, top), count - 1);
  let end = Math.max(start + 1, firstStartingAtOrAfter(offsets, count, bottom));
  end = Math.min(end, count);
  start = Math.min(start, end - 1);
  return { start, end, padTop: offsets[start], padBottom: total - offsets[end] };
}
