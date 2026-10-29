/**
 * The per-document write state (a counter and the writers in flight) exists in the stores but is invisible to every reader. These
 * checks need no database; the same guarantee against a real store is `document store contract (state hidden)` in
 * tests/stores/contract/document.contract.ts.
 */
import { expect } from 'chai';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { DocumentModel } from '../../../src/modules/storage/schema/document.schema';

const repoDir = join(__dirname, '../../../src/libs/repositories/document');

describe('document write state is hidden', () => {
  it('Mongo: both fields are select:false, so no query returns them unless it asks', () => {
    const seq = DocumentModel.schema.path('writeSeq');
    const writers = DocumentModel.schema.path('writers');
    expect(seq, 'writeSeq is in the schema').to.not.equal(undefined);
    expect(writers, 'writers is in the schema').to.not.equal(undefined);
    expect(seq.options.select).to.equal(false);
    expect(writers.options.select).to.equal(false);
  });

  it('Mongo: a new row gets no default for either field', () => {
    const row = new DocumentModel({ orgId: '650000000000000000000001', documentName: 'n', storageVendor: 'local' });
    expect(row.get('writeSeq')).to.equal(undefined);
    expect(row.get('writers')).to.equal(undefined);
    expect(JSON.stringify(row.toObject())).to.not.contain('writers');
  });

  it('F15c: the Mongo repository has no raw read that would bypass select:false', () => {
    const src = readFileSync(join(repoDir, 'mongo.ts'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
    expect(src, 'aggregate( returns documents without the schema').to.not.match(/\.aggregate\(/);
    expect(src, 'a raw collection read').to.not.match(/\.collection\b/);
  });

  it('PostgreSQL: the row mapper names neither column', () => {
    const src = readFileSync(join(repoDir, 'pg.ts'), 'utf8');
    const cols = /const COLS = `([^`]*)`/.exec(src)?.[1] ?? '';
    expect(cols).to.contain('updated_at');
    expect(cols).to.not.match(/write_seq|writers/);
    expect(src).to.not.match(/write_seq|\bwriters\b/);
  });
});
