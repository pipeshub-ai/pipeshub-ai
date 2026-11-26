/**
 * The coding-engine choices (R45-CARRY group 5). On MongoDB they are fields of the org, user and chat documents; on
 * PostgreSQL they are the harness's own rows (`harness.personal_choices`, `PgCodingEnginePreferences`), so main's tables
 * are not widened for them: one row per subject, `org` for the org's `defaultCodingEngine` and `codingEngineAutoRoute`,
 * `user:<id>` for a person's `codingEngine` and `harnessMemoryOff`, `session:<id>` for a chat's `codingEngine`, `codingEnginePin` and `repository`.
 * The row's `doc` holds those fields as the document held them.
 *
 * Every subject comes from one document, so a document's rows are written from it alone, removed with it, and given back to it
 * field by field at a rollback (fields PostgreSQL no longer holds are removed from the document).
 */
import type { Tx as KernelTx } from '../../../libs/db';
import {
  decodeDoc,
  docParam,
  rowRegion,
} from '../../../libs/repositories/agent-harness/pg-documents/pg';
import type { Carrier, CarryMongo, Doc, Restored, SourceDoc } from './types';

interface Source {
  paths: readonly string[];
  /** The subject's row key, and the organisation the row belongs to. */
  subject(src: SourceDoc): string;
  org(src: SourceDoc): string;
  /** The subjects of this collection in the table. */
  like: string;
}

const orgField =
  (collection: string) =>
  (src: SourceDoc): string => {
    const o = src.doc.orgId;
    if (typeof o !== 'string' || o === '')
      throw new Error(
        `${collection} ${src.id} holds a coding-engine field but no orgId: correct the document, then copy again`,
      );
    return o.toLowerCase();
  };

const SOURCES: Record<string, Source> = {
  org: {
    paths: ['defaultCodingEngine', 'codingEngineAutoRoute'],
    subject: () => 'org',
    org: (src) => src.id.toLowerCase(),
    like: 'org',
  },
  users: {
    paths: ['codingEngine', 'harnessMemoryOff'],
    subject: (src) => `user:${src.id}`,
    org: orgField('users'),
    like: 'user:%',
  },
  chatSessions: {
    paths: ['codingEngine', 'codingEnginePin', 'repository'],
    subject: (src) => `session:${src.id}`,
    org: orgField('chatSessions'),
    like: 'session:%',
  },
};

const source = (collection: string): Source => {
  const s = SOURCES[collection];
  if (!s) throw new Error(`coding-engine-choices does not carry ${collection}`);
  return s;
};

const picked = (s: Source, src: SourceDoc): Doc | null => {
  const out: Doc = {};
  for (const p of s.paths) if (src.doc[p] !== undefined) out[p] = src.doc[p];
  return Object.keys(out).length ? out : null;
};

async function writeChoices(
  k: KernelTx,
  s: Source,
  docs: SourceDoc[],
): Promise<void> {
  for (const src of docs) {
    const doc = picked(s, src);
    const subject = s.subject(src);
    if (s.like === 'org') {
      if (doc)
        await k.query(
          `/* pipeshub:migrate.carry_choices_put */ INSERT INTO harness.personal_choices (org_id, subject, doc, region)
           VALUES ($1, $2, $3::jsonb, $4) ON CONFLICT (org_id, subject) DO UPDATE SET doc = EXCLUDED.doc`,
          [s.org(src), subject, docParam(doc), rowRegion()],
        );
      else
        await k.query(
          `/* pipeshub:migrate.carry_choices_drop_org */ DELETE FROM harness.personal_choices WHERE org_id = $1 AND subject = 'org'`,
          [s.org(src)],
        );
      continue;
    }
    // a person or a chat names one subject; a row under another organisation (the document moved) is removed with the rest
    await k.query(
      `/* pipeshub:migrate.carry_choices_drop */ DELETE FROM harness.personal_choices WHERE subject = $1 AND ($2::text IS NULL OR org_id <> $2)`,
      [subject, doc ? s.org(src) : null],
    );
    if (doc)
      await k.query(
        `/* pipeshub:migrate.carry_choices_put */ INSERT INTO harness.personal_choices (org_id, subject, doc, region)
         VALUES ($1, $2, $3::jsonb, $4) ON CONFLICT (org_id, subject) DO UPDATE SET doc = EXCLUDED.doc`,
        [s.org(src), subject, docParam(doc), rowRegion()],
      );
  }
}

async function choiceRows(
  k: KernelTx,
  s: Source,
): Promise<Array<{ org_id: string; subject: string; doc: unknown }>> {
  const { rows } = await k.query<{
    org_id: string;
    subject: string;
    doc: unknown;
  }>(
    `/* pipeshub:migrate.carry_choices_rows */ SELECT org_id, subject, doc FROM harness.personal_choices WHERE subject LIKE $1`,
    [s.like],
  );
  return rows;
}

async function restore(
  k: KernelTx,
  collection: string,
  mongo: CarryMongo,
): Promise<Restored> {
  const s = source(collection);
  const want = new Map<string, Doc>();
  for (const r of await choiceRows(k, s))
    want.set(`${r.org_id}|${r.subject}`, decodeDoc<Doc>(r.doc));
  const ops: Array<{ id: string; oid: boolean; set: Doc; unset: string[] }> =
    [];
  for await (const src of mongo.scan(collection, {
    pick: [...s.paths, 'orgId'],
    idPaths: ['orgId'],
  })) {
    if (src.refused)
      throw new Error(`${src.refused}: the rollback stopped before writing it`);
    const set: Doc = {};
    const unset: string[] = [];
    // a document without an organisation has no row (the copy refused any field it held)
    const org =
      collection === 'org'
        ? src.id
        : typeof src.doc.orgId === 'string'
          ? src.doc.orgId.toLowerCase()
          : null;
    const row = org === null ? undefined : want.get(`${org}|${s.subject(src)}`);
    for (const p of s.paths) {
      if (row?.[p] !== undefined) set[p] = row[p];
      else if (src.doc[p] !== undefined) unset.push(p);
    }
    if (Object.keys(set).length || unset.length)
      ops.push({ id: src.id, oid: src.oid, set, unset });
  }
  for (let i = 0; i < ops.length; i += 500)
    await mongo.update(collection, ops.slice(i, i + 500));
  return {
    written: ops.filter((o) => Object.keys(o.set).length).length,
    removed: ops.filter((o) => !Object.keys(o.set).length).length,
  };
}

export const CODING_ENGINE_CHOICES: Carrier = {
  name: 'coding-engine-choices',
  reason:
    "the coding-engine choices of orgs, people and chats, kept in the harness's own rows on PostgreSQL",
  collections: [],
  fields: {
    org: SOURCES.org?.paths ?? [],
    users: SOURCES.users?.paths ?? [],
    chatSessions: SOURCES.chatSessions?.paths ?? [],
  },
  reads: {
    org: { pick: SOURCES.org?.paths ?? [] },
    users: {
      pick: [...(SOURCES.users?.paths ?? []), 'orgId'],
      idPaths: ['orgId'],
    },
    chatSessions: {
      pick: [...(SOURCES.chatSessions?.paths ?? []), 'orgId'],
      idPaths: ['orgId'],
    },
  },
  tables: ['harness.personal_choices'],
  async write(k, collection, docs) {
    await writeChoices(k, source(collection), docs);
  },
  async remove(k, collection, ids) {
    if (!ids.length) return;
    if (collection === 'org') {
      await k.query(
        `/* pipeshub:migrate.carry_choices_remove_org */ DELETE FROM harness.personal_choices WHERE subject = 'org' AND org_id = ANY($1::text[])`,
        [ids.map((i) => i.toLowerCase())],
      );
      return;
    }
    const prefix = collection === 'users' ? 'user:' : 'session:';
    source(collection);
    await k.query(
      `/* pipeshub:migrate.carry_choices_remove */ DELETE FROM harness.personal_choices WHERE subject = ANY($1::text[])`,
      [ids.map((i) => `${prefix}${i}`)],
    );
  },
  expected(collection, src) {
    const s = source(collection);
    const out: Array<[string, unknown]> = [];
    const doc = picked(s, src);
    if (doc) out.push([`${s.org(src)}|${s.subject(src)}`, doc]);
    return out;
  },
  async actual(k, collection) {
    const s = source(collection);
    const out = new Map<string, unknown>();
    for (const r of await choiceRows(k, s))
      out.set(`${r.org_id}|${r.subject}`, decodeDoc(r.doc));
    return out;
  },
  restore,
  async clear(k) {
    await k.query(
      `/* pipeshub:migrate.carry_choices_clear */ DELETE FROM harness.personal_choices`,
    );
  },
};
