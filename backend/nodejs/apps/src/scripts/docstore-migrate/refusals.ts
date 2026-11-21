/**
 * The copy's refusals (`platform.docstore_refused`, platform_data_0008, R48MIGFIX-FU-2): every document it left out, by collection and
 * source id, with the importer's reason. The job state lists only the first `MAX_SKIPPED_LISTED` of a collection, for the operator;
 * verify and rollback read this list, so a refused document past that cap never reads as missing, and a rollback never removes one from
 * MongoDB that PostgreSQL never held. Written in the unit that writes the rows, so a refusal commits with its batch; the follower keeps
 * it current (a document accepted later, or deleted, leaves it); removed with the job (reset, finalise).
 */
import type { Tx as KernelTx } from '../../libs/db';
import type { MigrationRuntime } from './runtime';

export interface Refusal {
  id: string;
  reason: string;
}

/** `refused` is now the list's for these documents, and `accepted` are no longer on it. */
export async function putRefusals(
  k: KernelTx,
  collection: string,
  refused: readonly Refusal[],
  accepted: readonly string[] = [],
): Promise<void> {
  if (accepted.length) await dropRefusals(k, collection, accepted);
  if (!refused.length) return;
  await k.query(
    `/* pipeshub:migrate.refused_put */ INSERT INTO platform.docstore_refused (collection, source_id, reason)
     SELECT $1, s, r FROM unnest($2::text[], $3::text[]) AS t(s, r)
     ON CONFLICT (collection, source_id) DO UPDATE SET reason = EXCLUDED.reason`,
    [collection, refused.map((x) => x.id), refused.map((x) => x.reason)],
  );
}

/** The collection's list becomes `refused` (a step that rewrites the whole collection, as the Slack links' follow does). */
export async function replaceRefusals(
  k: KernelTx,
  collection: string,
  refused: readonly Refusal[],
): Promise<void> {
  await k.query(
    `/* pipeshub:migrate.refused_clear_collection */ DELETE FROM platform.docstore_refused WHERE collection = $1`,
    [collection],
  );
  await putRefusals(k, collection, refused);
}

export async function dropRefusals(
  k: KernelTx,
  collection: string,
  ids: readonly string[],
): Promise<void> {
  if (!ids.length) return;
  await k.query(
    `/* pipeshub:migrate.refused_drop */ DELETE FROM platform.docstore_refused WHERE collection = $1 AND source_id = ANY($2::text[])`,
    [collection, ids],
  );
}

export async function clearRefusals(k: KernelTx): Promise<void> {
  await k.query(
    `/* pipeshub:migrate.refused_clear */ DELETE FROM platform.docstore_refused`,
  );
}

/**
 * Every document of `collection` the copy refused (id to reason): this list, and the job state's `listed` (a job written before the
 * list existed holds its first refusals only there).
 */
export async function refusedIds(
  rt: MigrationRuntime,
  collection: string,
  listed: Record<string, string> | undefined,
): Promise<Record<string, string>> {
  const rows = await rt.jobs(
    async (_t, k) =>
      (
        await k.query<{ source_id: string; reason: string }>(
          `/* pipeshub:migrate.refused_list */ SELECT source_id, reason FROM platform.docstore_refused WHERE collection = $1`,
          [collection],
        )
      ).rows,
    { write: false },
  );
  return {
    ...(listed ?? {}),
    ...Object.fromEntries(rows.map((r) => [r.source_id, r.reason])),
  };
}
