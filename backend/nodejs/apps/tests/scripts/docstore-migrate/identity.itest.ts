import 'reflect-metadata';
import { expect } from 'chai';
import { Pool } from 'pg';
import { PG_BINS, PgStack, haveStack, startPgStack } from '../../stores/pg-store-harness';
import { MAPPINGS } from '../../../src/scripts/docstore-migrate/mapping';
import { copyAndCompare } from './roundtrip';
import { ADMIN_A, identityDocs, MEMBER_A, USER_B } from './fixtures/identity';
import { IDENTITY_FOLLOW_UPS } from '../../../src/scripts/docstore-migrate/follow-ups/identity';
import { counterViolations, verifyCredentialRows } from '../../../src/scripts/docstore-migrate/verify-identity';
import { readDecoded } from '../../../src/scripts/docstore-migrate/pg-read';
import { compareDocs } from '../../../src/scripts/docstore-migrate/compare';
import { prepareSource, SourceReport } from '../../../src/scripts/docstore-migrate/source';
import { forOrg } from '../../../src/libs/context/orgContext';
import { withUow } from '../../../src/libs/db';
import { PgTx } from '../../../src/libs/repositories/pg';
import { jobsUnit } from './roundtrip';
import type { MigrationRuntime } from '../../../src/scripts/docstore-migrate/runtime';

const suite = haveStack ? describe : describe.skip;
const idMappings = MAPPINGS.filter((m) => m.table.startsWith('identity.'));

for (const bin of PG_BINS.length ? PG_BINS : ['none']) {
  suite(`docstore-migrate: identity collections (${bin})`, function () {
    this.timeout(240_000);
    let stack: PgStack;
    let migrate: Pool;
    const docs = identityDocs();
    let result: Awaited<ReturnType<typeof copyAndCompare>>;

    before(async () => {
      process.env.DOC_STORE = 'postgres';
      stack = await startPgStack(bin, 'migrate_identity', { importer: true });
      migrate = new Pool({ connectionString: stack.cluster.url(stack.db, 'pipeshub_migrate'), max: 2 });
      await migrate.query("SELECT platform.job_start('docstore_migration')");
      result = await copyAndCompare(docs, idMappings);
    });
    after(async () => {
      await migrate?.end();
      await stack?.stop();
    });

    const stubRt = (): MigrationRuntime =>
      ({
        migratePool: migrate,
        asOrg: (org: string, fn: (tx: PgTx, k: PgTx['kernel']) => Promise<void>) =>
          forOrg(org, 'test', () => withUow({ write: true }, (k) => fn(new PgTx(k), k))),
      }) as unknown as MigrationRuntime;

    it('copies every collection and reads back identical documents', () => {
      for (const [name, diffs] of Object.entries(result.diffs)) expect(diffs, name).to.deep.equal([]);
    });

    it('lists what it changed or left out', () => {
      expect(result.report.nul.get('users.fullName')).to.equal(1);
      expect(result.report.applied.get('member listed twice kept once')).to.equal(1);
      expect(result.report.applied.get('email lower-cased')).to.equal(1);
      expect(result.report.skipped.get('activity without an organisation')).to.equal(1);
      expect(result.inserted.userActivities['identity.user_activities']).to.equal(2);
      expect(result.inserted.userGroups['identity.user_group_members']).to.equal(6);
    });

    it('keeps the member order of a group without repeats', async () => {
      const r = await stack.admin((c) =>
        c.query("SELECT g.name, array_agg(m.user_id::text ORDER BY m.pos) AS u, array_agg(m.pos ORDER BY m.pos) AS p FROM identity.user_groups g JOIN identity.user_group_members m USING (org_id) WHERE m.group_id = g.id GROUP BY g.name"),
      );
      const admin = r.rows.find((x: { name: string }) => x.name === 'admin');
      expect(admin.p).to.deep.equal([0, 1]);
      expect(admin.u).to.have.length(2);
    });

    it('turns credentials into one row per account, the newest credential of an account winning', async () => {
      const { rows } = await stack.admin((c) => c.query('SELECT to_jsonb(c) AS r FROM identity.user_credentials c'));
      const diffs = verifyCredentialRows(docs.userCredentials, docs.users, rows.map((x) => x.r));
      expect(diffs).to.deep.equal([]);
      expect(rows.map((x) => x.r.user_id).sort()).to.deep.equal([String(ADMIN_A), String(MEMBER_A), String(USER_B)].sort());
      expect(rows.find((x) => x.r.user_id === String(MEMBER_A)).r.password_hash).to.equal('$2b$10$newerhash');
    });

    it('a credential row that differs from the newest credential shows as a difference', async () => {
      const { rows } = await stack.admin((c) => c.query('SELECT to_jsonb(c) AS r FROM identity.user_credentials c'));
      const edited = rows.map((x) => (x.r.user_id === String(ADMIN_A) ? { ...x.r, wrong_count: 9 } : x.r));
      const diffs = verifyCredentialRows(docs.userCredentials, docs.users, edited);
      expect(diffs.map((d) => d.id)).to.deep.equal([String(ADMIN_A)]);
    });

    it('seeds the slug counters above every slug, and the organisation sequence', async () => {
      await IDENTITY_FOLLOW_UPS.seed_org_slug_seq!(stubRt(), { kind: 'seed_org_slug_seq', seq: 1002 });
      const rows = (await stack.admin((c) => c.query("SELECT org_id, kind, next_value FROM identity.slug_counters ORDER BY org_id, kind"))).rows;
      expect(rows).to.have.length(4);
      expect(rows.filter((r: { kind: string }) => r.kind === 'User').every((r: { next_value: number }) => r.next_value === 1005)).to.equal(true);
      const seq = (await stack.admin((c) => c.query('SELECT last_value, is_called FROM identity.org_slug_seq'))).rows[0];
      expect(Number(seq.last_value)).to.equal(1003);
      expect(seq.is_called).to.equal(false);
      // running it again never lowers the sequence
      await IDENTITY_FOLLOW_UPS.seed_org_slug_seq!(stubRt(), { kind: 'seed_org_slug_seq', seq: 5 });
      expect(Number((await stack.admin((c) => c.query('SELECT last_value FROM identity.org_slug_seq'))).rows[0].last_value)).to.equal(1003);
      const viol = await counterViolations(async (sql) => (await stack.admin((c) => c.query(sql))).rows);
      expect(viol).to.deep.equal([]);
      expect(result.report.skipped.size).to.be.greaterThan(0);
    });

    it('copying the same documents again writes nothing new', async () => {
      const again = await copyAndCompare(docs, idMappings);
      for (const [name, byTable] of Object.entries(again.inserted))
        for (const [table, n] of Object.entries(byTable)) expect(n, `${name} ${table}`).to.equal(0);
    });

    it('a column the importer would drop shows up as a difference on exactly that user', async () => {
      await stack.admin((c) => c.query('UPDATE identity.users SET mobile = NULL WHERE id = $1', [String(MEMBER_A)]));
      const m = idMappings.find((x) => x.collection === 'users')!;
      const source = docs.users.map((d) => prepareSource(m, d, new SourceReport())!);
      const decoded = await jobsUnit((_tx, k) => readDecoded(k, m, source.map((d) => String(d._id))));
      const diffs = compareDocs(source, decoded);
      expect(diffs.map((d) => d.id)).to.deep.equal([String(MEMBER_A)]);
      expect(diffs[0]!.detail.join(' ')).to.contain('mobile');
    });
  });
}
