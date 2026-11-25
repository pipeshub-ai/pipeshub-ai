/**
 * Slack account links (ADR-SLACKID-01 D6-D8) are keyed by (organisation, Slack identity space, Slack account) in both stores, and
 * PostgreSQL keeps no document id. The same Slack account can be linked in several organisations, so no one column names a link,
 * and a change-stream delete (which carries only the `_id`) cannot say which row it removed. The collection is one row per linked
 * person, so every step works on the whole of it:
 *
 *  - verify compares by the full key (`orgId/workspaceId/slackUserId`), both ways, with counts per organisation;
 *  - follow replaces the table with what MongoDB holds whenever a link event arrives (an unlink must not survive the move: the bot
 *    would keep acting as that person);
 *  - rollback writes every row back by its key and removes the documents PostgreSQL no longer holds, for the same reason.
 *
 * A link the importer refuses (its person is not a copied user of its organisation) is a refusal like any skipped document
 * (`refusals.ts`, every one of them; the job state lists the first): verify does not count it as missing while PostgreSQL has no row for its key, and rollback leaves it in MongoDB, because
 * PostgreSQL never held it and so cannot have unlinked it.
 */
import { Doc } from './canonical';
import { compareDocs, DocDiff } from './compare';
import { MAX_SKIPPED_LISTED } from './bulk';
import { decodeDoc } from './decode';
import { importSlackUserLinkDocs } from '../../libs/repositories/slack-user-link/pg-import';
import type { CollectionMapping } from './mapping/types';
import type { MigrationRuntime } from './runtime';
import { SourceReport, prepareSource, toNeutral } from './source';
import type { JobState } from './state';
import { replaceRefusals } from './refusals';

export const SLACK_LINKS = 'slack_user_links';

type Row = Record<string, unknown>;

export const slackLinkKey = (d: Record<string, unknown>): string =>
  `${String(d.orgId ?? '').toLowerCase()}/${String(d.workspaceId ?? '')}/${String(d.slackUserId ?? '')}`;

/**
 * Source documents and table rows, both keyed by the link's full key, compared field by field. A document listed in `skips` (by its
 * `_id`) whose key has no row is the copy's own refusal, reported in `skippedListed`, not a difference; one that has a row since
 * (the follower copied it after its person arrived) is compared like any other.
 */
export function compareSlackLinks(
  m: CollectionMapping,
  raws: unknown[],
  rows: Row[],
  skips: Record<string, string> = {},
): {
  diffs: DocDiff[];
  perOrgSource: Map<string, number>;
  skippedListed: Array<{ id: string; reason: string }>;
} {
  const rep = new SourceReport();
  const perOrgSource = new Map<string, number>();
  const source: Doc[] = [];
  const skippedListed: Array<{ id: string; reason: string }> = [];
  const decoded = rows.map((r) => {
    const d = decodeDoc(m, r, {});
    return { ...d, _id: slackLinkKey(d) };
  });
  const inPg = new Set(decoded.map((d) => d._id));
  for (const raw of raws) {
    const d = prepareSource(m, raw, rep);
    if (!d) continue;
    const key = slackLinkKey(d);
    const id = String((raw as { _id?: unknown })._id).toLowerCase();
    const why = skips[id];
    if (why !== undefined && !inPg.has(key)) {
      skippedListed.push({ id, reason: why });
      continue;
    }
    const org = String(d.orgId ?? '');
    perOrgSource.set(org, (perOrgSource.get(org) ?? 0) + 1);
    source.push({ ...d, _id: key });
  }
  return { diffs: compareDocs(source, decoded), perOrgSource, skippedListed };
}

async function allDocs(rt: MigrationRuntime): Promise<unknown[]> {
  return rt
    .db()
    .collection(SLACK_LINKS)
    .find({}, { promoteLongs: false })
    .toArray();
}

async function allRows(rt: MigrationRuntime): Promise<Row[]> {
  return rt.jobs(
    async (_t, k) =>
      (
        await k.query<{ r: Row }>(
          '/* pipeshub:verify.slack_user_links */ SELECT to_jsonb(l) AS r FROM integrations.slack_user_links l',
        )
      ).rows.map((x) => x.r),
    { write: false },
  );
}

export async function verifySlackLinks(
  rt: MigrationRuntime,
  m: CollectionMapping,
  skips: Record<string, string> = {},
): Promise<{
  diffs: DocDiff[];
  skippedListed: Array<{ id: string; reason: string }>;
  sourceDocs: number;
  pgRows: number;
  perOrg: Record<string, number>;
  countMismatches: Array<{ org: string; source: number; pg: number }>;
}> {
  const raws = await allDocs(rt);
  const rows = await allRows(rt);
  const { diffs, perOrgSource, skippedListed } = compareSlackLinks(
    m,
    raws,
    rows,
    skips,
  );
  const perOrgPg = new Map<string, number>();
  for (const r of rows) {
    const o = String(r.org_id);
    perOrgPg.set(o, (perOrgPg.get(o) ?? 0) + 1);
  }
  const countMismatches: Array<{ org: string; source: number; pg: number }> =
    [];
  for (const o of new Set([...perOrgSource.keys(), ...perOrgPg.keys()])) {
    const s = perOrgSource.get(o) ?? 0;
    const p = perOrgPg.get(o) ?? 0;
    if (s !== p) countMismatches.push({ org: o, source: s, pg: p });
  }
  return {
    diffs,
    skippedListed,
    sourceDocs: raws.length,
    pgRows: rows.length,
    perOrg: Object.fromEntries(perOrgPg),
    countMismatches,
  };
}

/**
 * Follow: the table becomes what MongoDB holds now, in one unit (links removed there are removed here too). The links refused this
 * time replace the job's list, so verify and rollback see the same refusals as the table (saved at the caller's next checkpoint).
 */
export async function syncSlackLinks(
  rt: MigrationRuntime,
  js: JobState,
): Promise<number> {
  const rep = new SourceReport();
  const docs = (await allDocs(rt)).map((d) => toNeutral(d, rep, SLACK_LINKS));
  const stats = await rt.jobs(async (_t, k) => {
    await k.query(
      '/* pipeshub:follow.slack_user_links_clear */ DELETE FROM integrations.slack_user_links',
    );
    const st = await importSlackUserLinkDocs(k, SLACK_LINKS, docs);
    await replaceRefusals(
      k,
      SLACK_LINKS,
      st.skipped.map((sk) => ({ id: sk.id.toLowerCase(), reason: sk.reason })),
    );
    return st;
  });
  const p = (js.progress.collections[SLACK_LINKS] ??= {
    state: 'done',
    last: null,
    copied: 0,
    skipped: 0,
    rows: {},
  });
  p.skippedIds = Object.fromEntries(
    stats.skipped
      .slice(0, MAX_SKIPPED_LISTED)
      .map((sk) => [sk.id.toLowerCase(), sk.reason]),
  );
  p.skipped = stats.skipped.length;
  return docs.length;
}

const toDates = (d: Record<string, unknown>): Record<string, unknown> => {
  for (const k of ['linkedAt', 'createdAt', 'updatedAt'])
    if (typeof d[k] === 'string') d[k] = new Date(d[k]);
  return d;
};

/**
 * Rollback: every row written back by its key; a document whose row is gone (unlinked after the move) is removed, unless the copy
 * refused and listed it (`kept`, by `_id`): PostgreSQL never held that one.
 */
export async function reverseSlackLinks(
  rt: MigrationRuntime,
  m: CollectionMapping,
  kept: Set<string> = new Set(),
): Promise<{ written: number; removed: number }> {
  const col = rt.db().collection(SLACK_LINKS);
  const rows = await allRows(rt);
  const keep = new Set<string>();
  let written = 0;
  for (const r of rows) {
    const { _id: _unused, ...d } = decodeDoc(m, r, {});
    keep.add(slackLinkKey(d));
    await col.replaceOne(
      {
        orgId: d.orgId,
        workspaceId: d.workspaceId,
        slackUserId: d.slackUserId,
      },
      toDates(d),
      { upsert: true },
    );
    written += 1;
  }
  const gone = (
    await col
      .find({}, { projection: { orgId: 1, workspaceId: 1, slackUserId: 1 } })
      .toArray()
  ).filter(
    (d) => !keep.has(slackLinkKey(d)) && !kept.has(String(d._id).toLowerCase()),
  );
  const r = gone.length
    ? await col.deleteMany({ _id: { $in: gone.map((d) => d._id) } })
    : { deletedCount: 0 };
  return { written, removed: r.deletedCount };
}
