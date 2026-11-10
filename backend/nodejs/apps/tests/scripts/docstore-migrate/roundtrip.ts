/**
 * Shared by the collection tests: raw MongoDB-shaped documents are copied with the repositories' `importBatch` into a real
 * PostgreSQL and read back with the verifier's own decoder; the decoded documents must equal the prepared source documents.
 */
import { expect } from 'chai';
import { ObjectId } from 'mongodb';
import { REPOSITORIES } from '../../../src/libs/repositories/all';
import type { Importable } from '../../../src/libs/repositories/import';
import { MAPPINGS } from '../../../src/scripts/docstore-migrate/mapping';
import type { CollectionMapping } from '../../../src/scripts/docstore-migrate/mapping/types';
import { SourceReport, prepareSource, toNeutral } from '../../../src/scripts/docstore-migrate/source';
import { readDecoded } from '../../../src/scripts/docstore-migrate/pg-read';
import { compareDocs, DocDiff } from '../../../src/scripts/docstore-migrate/compare';
import { withJobsUow } from '../../../src/libs/db';
import { system } from '../../../src/libs/context/orgContext';
import { PgTx } from '../../../src/libs/repositories/pg';

export const oid = (hex?: string): ObjectId => (hex ? new ObjectId(hex) : new ObjectId());
export const at = (iso: string): Date => new Date(iso);

export interface CopyResult {
  diffs: Record<string, DocDiff[]>;
  report: SourceReport;
  inserted: Record<string, Record<string, number>>;
}

export const jobsUnit = <T>(fn: (tx: PgTx, k: PgTx['kernel']) => Promise<T>): Promise<T> =>
  system('test', () => withJobsUow({ write: true, service: 'import' }, (k) => fn(new PgTx(k), k)));

/** Copies `docs` (collection name to raw documents) in mapping order, then decodes every copied document and compares. */
export async function copyAndCompare(docs: Record<string, unknown[]>, mappings: CollectionMapping[] = MAPPINGS): Promise<CopyResult> {
  const report = new SourceReport();
  const inserted: CopyResult['inserted'] = {};
  const diffs: CopyResult['diffs'] = {};
  const todo = mappings.filter((m) => docs[m.collection]?.length);
  for (const m of todo) {
    const repo = REPOSITORIES[m.repository]() as Importable;
    expect(repo.importBatch, `${m.repository} has no importBatch`).to.be.a('function');
    const neutralDocs = docs[m.collection].map((d) => toNeutral(d, new SourceReport(), m.collection));
    inserted[m.collection] = (await jobsUnit((tx) => repo.importBatch(m.collection, neutralDocs, tx))).inserted;
  }
  for (const m of todo) {
    const source = docs[m.collection].map((d) => prepareSource(m, d, report)).filter((d): d is NonNullable<typeof d> => d !== null);
    const decoded = await jobsUnit((_tx, k) => readDecoded(k, m, source.map((d) => String(d._id))));
    diffs[m.collection] = compareDocs(source, decoded);
  }
  return { diffs, report, inserted };
}

/** A second copy of the same documents writes nothing and changes nothing. */
export async function copyAgainInserts(docs: Record<string, unknown[]>, mappings: CollectionMapping[] = MAPPINGS): Promise<number> {
  let n = 0;
  for (const m of mappings.filter((x) => docs[x.collection]?.length)) {
    const repo = REPOSITORIES[m.repository]() as Importable;
    const neutralDocs = docs[m.collection].map((d) => toNeutral(d, new SourceReport(), m.collection));
    const st = await jobsUnit((tx) => repo.importBatch(m.collection, neutralDocs, tx));
    n += Object.values(st.inserted).reduce((a, b) => a + b, 0);
  }
  return n;
}
