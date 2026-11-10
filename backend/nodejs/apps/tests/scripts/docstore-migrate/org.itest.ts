import 'reflect-metadata';
import { expect } from 'chai';
import { PG_BINS, PgStack, haveStack, startPgStack } from '../../stores/pg-store-harness';
import { copyAgainInserts, copyAndCompare } from './roundtrip';
import { identityDocs } from './fixtures/identity';

const suite = haveStack ? describe : describe.skip;

for (const bin of PG_BINS.length ? PG_BINS : ['none']) {
  suite(`docstore-migrate: organisations (${bin})`, function () {
    this.timeout(180_000);
    let stack: PgStack;
    before(async () => {
      process.env.DOC_STORE = 'postgres';
      stack = await startPgStack(bin, 'migrate_org', { importer: true });
    });
    after(async () => {
      await stack?.stop();
    });

    it('copies organisations and logos and reads back identical documents', async () => {
      const docs = identityDocs();
      const r = await copyAndCompare({ org: docs.org, 'org-logos': docs['org-logos'] });
      expect(r.diffs.org).to.deep.equal([]);
      expect(r.diffs['org-logos']).to.deep.equal([]);
      expect(r.inserted.org['identity.orgs']).to.equal(2);
      expect(await copyAgainInserts({ org: docs.org, 'org-logos': docs['org-logos'] })).to.equal(0);
    });
  });
}
