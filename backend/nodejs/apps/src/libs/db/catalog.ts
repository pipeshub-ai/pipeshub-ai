import { writeFileSync } from 'node:fs';
import { sqlTag } from './lint';

export interface CatalogEntry {
  tag: string;
  sql: string;
  args: unknown[];
}

/**
 * Statements seen by the unit of work, for the planner gate in CI. A repository test suite attaches one,
 * runs, and calls `write(path)`; the gate plans each entry under the app role. The first sample wins per tag.
 */
export class QueryCatalog {
  private readonly byTag = new Map<string, CatalogEntry>();
  readonly untagged = new Set<string>();

  record(sql: string, args: unknown[]): void {
    const tag = sqlTag(sql);
    if (!tag) this.untagged.add(sql);
    else if (!this.byTag.has(tag)) this.byTag.set(tag, { tag, sql, args });
  }

  toJSON(): { version: 1; statements: CatalogEntry[] } {
    const statements = [...this.byTag.values()].sort((a, b) =>
      a.tag.localeCompare(b.tag),
    );
    return { version: 1, statements };
  }

  write(path: string): void {
    writeFileSync(path, JSON.stringify(this, null, 2));
  }
}
