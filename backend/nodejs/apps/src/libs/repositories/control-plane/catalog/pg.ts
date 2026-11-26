import {
  CatalogItem,
  catalogItemSchema,
} from '../../../../modules/control-plane/domain/catalog-item';
import { ItemExists } from '../../../../modules/control-plane/domain/catalog-errors';
import {
  CatalogFilter,
  CatalogSortKey,
  nameKey,
} from '../../../../modules/control-plane/domain/catalog-order';
import type {
  CatalogWriteTx,
  OrgCatalogRows,
} from '../../../../modules/control-plane/ports/catalog-store';
import { rowRegion } from '../../agent-harness/pg-documents/pg';
import { Params } from '../../pg-util';
import { cpUnit } from '../pg-units';

interface Row {
  body: string;
  deprecated: boolean;
}

const fromRow = (row: Row): CatalogItem =>
  catalogItemSchema.parse({
    ...(JSON.parse(row.body) as object),
    deprecated: row.deprecated,
  });

/** An org's catalog rows in `harness.cp_catalog_items`, in catalog order (kind, lower-cased name, ref; byte order). */
export class PgOrgCatalogStore implements OrgCatalogRows {
  get(orgId: string, ref: string): Promise<CatalogItem | null> {
    return cpUnit(undefined, orgId, { write: false }, async (k) => {
      const { rows } = await k.query<Row>(
        `/* pipeshub:harness.cp_catalog_get */ SELECT body, deprecated FROM harness.cp_catalog_items WHERE org_id = $1 AND ref = $2`,
        [orgId, ref],
      );
      return rows[0] === undefined ? null : fromRow(rows[0]);
    });
  }

  page(
    orgId: string,
    filter: CatalogFilter,
    after: CatalogSortKey | null,
    limit: number,
  ): Promise<CatalogItem[]> {
    const p = new Params();
    const where = [`org_id = ${p.add(orgId)}`];
    if (after !== null)
      where.push(
        `(kind, sort_name, ref) > (${p.add(after[0])}::text, ${p.add(after[1])}::text, ${p.add(after[2])}::text)`,
      );
    if (filter.kind !== undefined) where.push(`kind = ${p.add(filter.kind)}`);
    if (filter.q !== undefined)
      where.push(`strpos(sort_name, ${p.add(filter.q.toLowerCase())}) > 0`);
    const text = `/* pipeshub:harness.cp_catalog_page */ SELECT body, deprecated FROM harness.cp_catalog_items
      WHERE ${where.join(' AND ')} ORDER BY kind, sort_name, ref LIMIT ${p.add(limit)}`;
    return cpUnit(undefined, orgId, { write: false }, async (k) =>
      (await k.query<Row>(text, p.values)).rows.map(fromRow),
    );
  }

  versions(
    orgId: string,
    kind: string,
    publisher: string,
    name: string,
  ): Promise<CatalogItem[]> {
    return cpUnit(undefined, orgId, { write: false }, async (k) => {
      const { rows } = await k.query<Row>(
        `/* pipeshub:harness.cp_catalog_versions */ SELECT body, deprecated FROM harness.cp_catalog_items
          WHERE org_id = $1 AND kind = $2 AND publisher = $3 AND name = $4 ORDER BY created_at, kind, sort_name, ref`,
        [orgId, kind, publisher, name],
      );
      return rows.map(fromRow);
    });
  }

  async insert(tx: CatalogWriteTx, item: CatalogItem): Promise<void> {
    const { deprecated, ...body } = item;
    await tx.enlist(() =>
      cpUnit(tx.session, tx.orgId, { write: true }, async (k) => {
        const inserted = await k.query(
          `/* pipeshub:harness.cp_catalog_insert */ INSERT INTO harness.cp_catalog_items
             (org_id, ref, kind, publisher, name, version, sort_name, created_at, deprecated, body, region)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11) ON CONFLICT DO NOTHING`,
          [
            tx.orgId,
            item.ref,
            item.kind,
            item.publisher,
            item.name,
            item.version,
            nameKey(item),
            item.createdAt,
            deprecated,
            JSON.stringify(body),
            rowRegion(),
          ],
        );
        if (inserted.rowCount !== 1) throw new ItemExists(`${item.ref} exists`);
      }),
    );
  }

  deprecate(tx: CatalogWriteTx, orgId: string, ref: string): Promise<boolean> {
    return cpUnit(tx.session, orgId, { write: true }, async (k) => {
      const exists = await k.query(
        `/* pipeshub:harness.cp_catalog_exists */ SELECT 1 FROM harness.cp_catalog_items WHERE org_id = $1 AND ref = $2`,
        [orgId, ref],
      );
      if (exists.rows.length === 0) return false;
      await tx.enlist(async () => {
        await k.query(
          `/* pipeshub:harness.cp_catalog_deprecate */ UPDATE harness.cp_catalog_items SET deprecated = true WHERE org_id = $1 AND ref = $2`,
          [orgId, ref],
        );
      });
      return true;
    });
  }
}
