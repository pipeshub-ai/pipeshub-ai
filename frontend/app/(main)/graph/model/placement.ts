// Where an expanded node goes: the next free grid slot on the side it belongs to.
// Never moves a node that is already placed, cannot overlap by construction, and
// gives the same picture for the same data (ids are sorted, not taken in server order).
// Layout stays out of the renderer: a cell is multiplied by the cell size at draw time.

export interface Cell {
  c: number;
  r: number;
}

/** -1 puts new nodes to the left of the parent (callers), +1 to the right (callees). */
export type Side = -1 | 1;

export const CELL_WIDTH = 240;
export const CELL_HEIGHT = 72;

/**
 * Rows either side of the parent a column of `total` neighbours uses, so the block they make is about as
 * wide as it is tall (a cell is 240 by 72): a single tall column fits to a speck, and so does a very wide strip.
 * 50 neighbours: 4 columns of 13; 172: 8 columns of 22.
 */
export function rowsEitherSide(total: number): number {
  const columns = Math.max(1, Math.ceil(Math.sqrt((total * CELL_HEIGHT) / CELL_WIDTH)));
  return Math.max(3, Math.ceil(Math.ceil(total / columns) / 2));
}

export const cellKey = (cell: Cell): string => `${cell.c},${cell.r}`;

function* slots(origin: Cell, side: Side, rows: number): Generator<Cell> {
  for (let column = 1; ; column++) {
    yield { c: origin.c + side * column, r: origin.r };
    for (let row = 1; row <= rows; row++) {
      yield { c: origin.c + side * column, r: origin.r + row };
      yield { c: origin.c + side * column, r: origin.r - row };
    }
  }
}

/** The cell of each of `ids` (sorted), taken from `occupied`, which is updated. */
export function placeNodes(
  occupied: Set<string>,
  origin: Cell,
  side: Side,
  ids: readonly string[],
  /** How many neighbours this side of the parent will hold in all, shown and to come, for the column height. */
  total: number = ids.length,
): Record<string, Cell> {
  const placed: Record<string, Cell> = {};
  const free = slots(origin, side, rowsEitherSide(total));
  for (const id of [...ids].sort()) {
    for (;;) {
      const cell = free.next().value as Cell;
      if (occupied.has(cellKey(cell))) continue;
      occupied.add(cellKey(cell));
      placed[id] = cell;
      break;
    }
  }
  return placed;
}
