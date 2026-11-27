/**
 * The dimension registry: `policy-dimensions.json` (generated as
 * `POLICY_DIMENSIONS`) read once and checked, with each row's order built.
 * A row's index is its refusal order.
 */
import type { LayerName } from '../../contracts/generated/core/enums';
import type { ParentDimensions } from '../../../control-plane';
import { POLICY_DIMENSIONS } from '../../contracts/generated/core/policy-data';
import type { PolicyDimensions } from '../../contracts/generated/core/policy/policy-dimensions';
import { orderOf, type Order } from './orders';

export type DimensionRow = PolicyDimensions['dimensions'][number];

export interface Dimension {
  /** Where the value sits in the release layer and in the effective config. */
  readonly path: string;
  /** Where a layer below the release carries it. */
  readonly layerPath: string;
  readonly kind: DimensionRow['kind'];
  readonly order: Order | null;
  readonly within: string | null;
  readonly widening: NonNullable<DimensionRow['widening']>;
  readonly exposure: ReadonlySet<LayerName>;
}

export class InvalidDimensionTable extends Error {}

const check = (ok: boolean, path: string, message: string): void => {
  if (!ok) throw new InvalidDimensionTable(`${path}: ${message}`);
};

function checked(rows: readonly DimensionRow[]): void {
  const paths = new Set<string>();
  const ceilings = new Set(
    rows.filter((r) => r.kind === 'ceiling').map((r) => r.path),
  );
  for (const row of rows) {
    check(!paths.has(row.path), row.path, 'appears twice');
    paths.add(row.path);
    check(
      (row.order !== undefined) === (row.kind === 'ceiling'),
      row.path,
      'a ceiling and only a ceiling has an order',
    );
    check(
      (row.within !== undefined) === (row.kind === 'choice'),
      row.path,
      'a choice and only a choice is within a ceiling',
    );
    if (row.within !== undefined) {
      check(
        ceilings.has(row.within),
        row.path,
        `${row.within} is not a ceiling`,
      );
    }
    if (row.kind === 'operator') {
      check(
        row.exposure.length === 1 && row.exposure[0] === 'deployment',
        row.path,
        'an operator row is set by the deployment only',
      );
    }
    check(
      !row.exposure.includes('release'),
      row.path,
      'the release is not a layer that narrows',
    );
    check(
      row.widening !== 'meet' || row.kind === 'ceiling',
      row.path,
      'only a ceiling meets',
    );
    const ranks = row.order?.ranks ?? [];
    const ranked = ranks.flat();
    check(
      new Set(ranked).size === ranked.length,
      row.path,
      'a ranked value appears twice',
    );
    check(
      ['rank', 'rank-raise', 'rank-raise-rules'].includes(
        row.order?.kind ?? '',
      ) ===
        ranks.length > 0,
      row.path,
      'a rank order and only a rank order lists ranks',
    );
  }
}

const relative = (path: string, prefix: string): string =>
  prefix === '' ? path : path.slice(prefix.length + 1);

export class DimensionRegistry {
  private readonly byPath: ReadonlyMap<string, Dimension>;

  private constructor(private readonly dimensions: readonly Dimension[]) {
    this.byPath = new Map(dimensions.map((d) => [d.path, d]));
  }

  /** Every row of `table`, checked; refuses a table that breaks a rule, naming the row. */
  static fromTable(table: PolicyDimensions, prefix = ''): DimensionRegistry {
    checked(table.dimensions);
    const under = (path: string): boolean =>
      prefix === '' || path.startsWith(`${prefix}.`);
    return new DimensionRegistry(
      table.dimensions
        .filter((row) => under(row.path))
        .map((row) => ({
          path: relative(row.path, prefix),
          layerPath: relative(row.narrowedBy ?? row.path, prefix),
          kind: row.kind,
          order: row.order === undefined ? null : orderOf(row.order),
          within:
            row.within === undefined ? null : relative(row.within, prefix),
          widening: row.widening ?? 'refuse',
          exposure: new Set(row.exposure),
        })),
    );
  }

  all(): readonly Dimension[] {
    return this.dimensions;
  }

  /** The row at `path`, if the table has one. */
  find(path: string): Dimension | undefined {
    return this.byPath.get(path);
  }

  get(path: string): Dimension {
    const found = this.find(path);
    if (found === undefined)
      throw new InvalidDimensionTable(`${path}: no such dimension`);
    return found;
  }
}

/** The release's dimension table. */
export const POLICY_REGISTRY = DimensionRegistry.fromTable(POLICY_DIMENSIONS);

/**
 * The table as a child's pin reads it: where a layer carries a dimension,
 * and whether a `parent` layer may set it. A parent's pin is stored data and
 * may hold a path this release's table lacks (a pin made by a newer release,
 * read after a rollback): no layer of this release can carry it, so it is
 * not a `parent` layer's to set and reaches the child as the parent's entry,
 * unchanged, as Python's `inherited_dimensions` reads it.
 */
export const parentDimensions = (
  registry: DimensionRegistry,
): ParentDimensions => ({
  layerPathOf: (path: string): string => registry.find(path)?.layerPath ?? path,
  exposedToParent: (path: string): boolean =>
    registry.find(path)?.exposure.has('parent') ?? false,
});

export const PARENT_DIMENSIONS = parentDimensions(POLICY_REGISTRY);
