/**
 * The key map (`platform.docstore_keymap`, platform_data_0006): for a table that does not keep the MongoDB `_id`, which row each
 * source document became. A change-stream delete names only the `_id`, so without it the follower could not remove the row (an
 * erasure during the follow would survive the move), and a rollback could not give a row back the id its document had. It is
 * written in the same unit as the rows, kept for the follow and rollback window, and removed with the job (`reset`, rollback,
 * finalise).
 *
 * A row no org owns (a deployment-wide table: no `org_id`) is mapped in the deployment-wide form
 * (`platform.docstore_keymap_deployment`, platform_data_0007): the same entry without an organisation. The org-scoped map cannot
 * hold it: its `org_id` is checked as an org id, and a made-up one would be an org the row-level policies know nothing of.
 */
import type { Tx as KernelTx } from '../../../libs/db';

export interface KeyEntry {
  /** null: a deployment-wide row (the deployment-wide form). */
  orgId: string | null;
  sourceId: string;
  /** The source `_id` was an ObjectId (it is given back as one). */
  oid: boolean;
  /** The row's key, as `rowKey` writes it. */
  rowKey: string;
}

/** The organisation `keysByRow` names a deployment-wide entry by (never an org id: those are checked as hex). */
export const DEPLOYMENT_KEY = '(deployment)';

/** A row's key as one text: its key values in order. */
export const rowKey = (values: readonly unknown[]): string =>
  JSON.stringify(values);

interface MapRow {
  org_id: string | null;
  source_id: string;
  source_oid: boolean;
  row_key: string;
}

const entryOf = (r: MapRow): KeyEntry => ({
  orgId: r.org_id,
  sourceId: r.source_id,
  oid: r.source_oid,
  rowKey: r.row_key,
});

export async function putKeys(
  k: KernelTx,
  collection: string,
  entries: KeyEntry[],
): Promise<void> {
  const org = entries.filter((e) => e.orgId !== null);
  const wide = entries.filter((e) => e.orgId === null);
  if (org.length)
    await k.query(
      `/* pipeshub:migrate.keymap_put */ INSERT INTO platform.docstore_keymap (org_id, collection, source_id, source_oid, row_key)
       SELECT o, $1, s, x, r FROM unnest($2::text[], $3::text[], $4::boolean[], $5::text[]) AS t(o, s, x, r)
       ON CONFLICT (org_id, collection, source_id) DO UPDATE SET row_key = EXCLUDED.row_key, source_oid = EXCLUDED.source_oid`,
      [
        collection,
        org.map((e) => e.orgId),
        org.map((e) => e.sourceId),
        org.map((e) => e.oid),
        org.map((e) => e.rowKey),
      ],
    );
  if (wide.length)
    await k.query(
      `/* pipeshub:migrate.keymap_put_deployment */ INSERT INTO platform.docstore_keymap_deployment (collection, source_id, source_oid, row_key)
       SELECT $1, s, x, r FROM unnest($2::text[], $3::boolean[], $4::text[]) AS t(s, x, r)
       ON CONFLICT (collection, source_id) DO UPDATE SET row_key = EXCLUDED.row_key, source_oid = EXCLUDED.source_oid`,
      [
        collection,
        wide.map((e) => e.sourceId),
        wide.map((e) => e.oid),
        wide.map((e) => e.rowKey),
      ],
    );
}

/** The entries of these source ids (any organisation, or none: a delete event does not say which). */
export async function keysOf(
  k: KernelTx,
  collection: string,
  sourceIds: string[],
): Promise<KeyEntry[]> {
  if (!sourceIds.length) return [];
  const { rows } = await k.query<MapRow>(
    `/* pipeshub:migrate.keymap_get */ SELECT org_id, source_id, source_oid, row_key FROM (SELECT org_id, collection, source_id, source_oid, row_key FROM platform.docstore_keymap
        UNION ALL SELECT NULL::text, collection, source_id, source_oid, row_key FROM platform.docstore_keymap_deployment) m
      WHERE collection = $1 AND source_id = ANY($2::text[])`,
    [collection, sourceIds],
  );
  return rows.map(entryOf);
}

/** Every entry of a collection, by organisation (`DEPLOYMENT_KEY` for the deployment-wide form) and row key. */
export async function keysByRow(
  k: KernelTx,
  collection: string,
): Promise<Map<string, KeyEntry>> {
  const { rows } = await k.query<MapRow>(
    `/* pipeshub:migrate.keymap_all */ SELECT org_id, source_id, source_oid, row_key FROM (SELECT org_id, collection, source_id, source_oid, row_key FROM platform.docstore_keymap
        UNION ALL SELECT NULL::text, collection, source_id, source_oid, row_key FROM platform.docstore_keymap_deployment) m
      WHERE collection = $1`,
    [collection],
  );
  return new Map(
    rows.map((r) => [`${r.org_id ?? DEPLOYMENT_KEY}|${r.row_key}`, entryOf(r)]),
  );
}

export async function dropKeys(
  k: KernelTx,
  collection: string,
  sourceIds: string[],
): Promise<void> {
  if (!sourceIds.length) return;
  await k.query(
    `/* pipeshub:migrate.keymap_drop */ DELETE FROM platform.docstore_keymap WHERE collection = $1 AND source_id = ANY($2::text[])`,
    [collection, sourceIds],
  );
  await k.query(
    `/* pipeshub:migrate.keymap_drop_deployment */ DELETE FROM platform.docstore_keymap_deployment WHERE collection = $1 AND source_id = ANY($2::text[])`,
    [collection, sourceIds],
  );
}

/** Removes the map (both forms) of the collections named, or all of it. */
export async function clearKeys(
  k: KernelTx,
  collections?: readonly string[],
): Promise<void> {
  if (collections) {
    await k.query(
      `/* pipeshub:migrate.keymap_clear */ DELETE FROM platform.docstore_keymap WHERE collection = ANY($1::text[])`,
      [collections],
    );
    await k.query(
      `/* pipeshub:migrate.keymap_clear_deployment */ DELETE FROM platform.docstore_keymap_deployment WHERE collection = ANY($1::text[])`,
      [collections],
    );
    return;
  }
  await k.query(
    `/* pipeshub:migrate.keymap_clear_all */ DELETE FROM platform.docstore_keymap`,
  );
  await k.query(
    `/* pipeshub:migrate.keymap_clear_all_deployment */ DELETE FROM platform.docstore_keymap_deployment`,
  );
}
