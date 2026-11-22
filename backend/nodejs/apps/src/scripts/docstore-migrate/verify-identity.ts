/**
 * Verifier parts for the identity collections that do not map document to row one for one: the slug counters, and the credentials in
 * their plain form (`verifyCredentialRows`, the same rule `credentials.ts` applies page by page). They take plain query results, so the
 * caller chooses the login that reads the tables and the sequence.
 */
import { Doc, plainJson } from './canonical';
import { compareDocs, DocDiff } from './compare';
import { decodeDoc } from './decode';
import { userCredentials } from './mapping/identity';
import { SourceReport, toCanonical } from './source';

type Row = Record<string, unknown>;

/** The credential documents that become rows: live credentials of live users, the newest per user. */
export function expectedCredentials(creds: unknown[], users: unknown[]): Doc[] {
  const rep = new SourceReport();
  const live = new Set<string>();
  for (const raw of users) {
    const u = toCanonical(raw, rep, 'users');
    if (u.isDeleted !== true) live.add(`${String(u.orgId)}/${String(u._id)}`);
  }
  const best = new Map<string, Doc>();
  for (const raw of creds) {
    const c = toCanonical(raw, rep, 'userCredentials');
    if (c.isDeleted === true || typeof c.userId !== 'string') continue;
    if (!live.has(`${String(c.orgId)}/${c.userId}`)) continue;
    const prev = best.get(c.userId);
    if (
      !prev ||
      Date.parse(String(c.updatedAt ?? 0)) >=
        Date.parse(String(prev.updatedAt ?? 0))
    )
      best.set(c.userId, c);
  }
  return [...best].map(([u, c]) => {
    const doc: Doc = { _id: u };
    for (const f of userCredentials.fields) {
      let v = c[f.path];
      if (v === undefined && f.readAbsentAs !== undefined) v = f.readAbsentAs;
      if (v !== undefined) doc[f.path] = v;
    }
    return plainJson(doc) as Doc;
  });
}

/** `rows` are `to_jsonb(identity.user_credentials)` rows. */
export function verifyCredentialRows(
  creds: unknown[],
  users: unknown[],
  rows: Row[],
): DocDiff[] {
  return compareDocs(
    expectedCredentials(creds, users),
    rows.map((r) => decodeDoc(userCredentials, r, {})),
  );
}

export const COUNTER_VIOLATIONS_SQL = [
  `SELECT 'org slug sequence is not above org-' || n AS problem FROM (SELECT max(substring(slug from '[0-9]+$')::bigint) AS n FROM identity.orgs WHERE slug ~ '^org-[0-9]+$') o,
     (SELECT CASE WHEN is_called THEN last_value + 1 ELSE last_value END AS nxt FROM identity.org_slug_seq) s WHERE o.n IS NOT NULL AND s.nxt <= o.n`,
  `SELECT 'user counter of ' || c.org_id || ' is not above its slug user-' || m.n AS problem FROM identity.slug_counters c
     JOIN LATERAL (SELECT max(substring(slug from '[0-9]+$')::int) AS n FROM identity.users u WHERE u.org_id = c.org_id AND u.slug ~ '^user-[0-9]+$') m ON true
     WHERE c.kind = 'User' AND m.n IS NOT NULL AND c.next_value < m.n`,
  `SELECT 'group counter of ' || c.org_id || ' is not above its slug usergroup-' || m.n AS problem FROM identity.slug_counters c
     JOIN LATERAL (SELECT max(substring(slug from '[0-9]+$')::int) AS n FROM identity.user_groups g WHERE g.org_id = c.org_id AND g.slug ~ '^usergroup-[0-9]+$') m ON true
     WHERE c.kind = 'UserGroup' AND m.n IS NOT NULL AND c.next_value < m.n`,
];

/** Runs the counter checks with the caller's query function (any role that reads these tables and the sequence). */
export async function counterViolations(
  query: (sql: string) => Promise<Row[]>,
): Promise<string[]> {
  const out: string[] = [];
  for (const sql of COUNTER_VIOLATIONS_SQL)
    for (const r of await query(sql)) out.push(String(r.problem));
  return out;
}
