/**
 * Documents, projects, notifications, the outbox, the mail log, connector settings, OAuth apps and tokens, delegations and Slack
 * links: copied with the repositories' `importBatch` into a real PostgreSQL and read back by the verifier's own decoder.
 *
 * The copy runs as the jobs login, which platform_data_0002 gives the row policy and privileges it needs on these tables.
 */
import 'reflect-metadata';
import { withUpsert } from '../../../src/libs/repositories/import';
import { createHash } from 'node:crypto';
import { expect } from 'chai';
import { PG_BINS, PgStack, haveStack, startPgStack } from '../../stores/pg-store-harness';
import { MAPPINGS } from '../../../src/scripts/docstore-migrate/mapping';
import { CONTENT_IGNORED, CONTENT_MAPPINGS } from '../../../src/scripts/docstore-migrate/mapping/content';
import { REPOSITORIES } from '../../../src/libs/repositories/all';
import type { Importable } from '../../../src/libs/repositories/import';
import { SourceReport, prepareSource, toNeutral } from '../../../src/scripts/docstore-migrate/source';
import { decodeDoc } from '../../../src/scripts/docstore-migrate/decode';
import { readDecoded } from '../../../src/scripts/docstore-migrate/pg-read';
import { compareDocs } from '../../../src/scripts/docstore-migrate/compare';
import { compareSlackLinks } from '../../../src/scripts/docstore-migrate/slack-links';
import { copyAgainInserts, copyAndCompare, jobsUnit, oid } from './roundtrip';
import { ORG_A, ORG_B, SESSION_1, SESSION_2, USER_1, USER_2, contentDocs, ids } from './fixtures/content';

const suite = haveStack ? describe : describe.skip;
const sha = (s: string): string => createHash('sha256').update(s).digest('hex');

describe('docstore-migrate: content mappings (no database)', () => {
  it('lists every collection of the group once and ignores one with a reason', () => {
    const names = CONTENT_MAPPINGS.map((m) => m.collection);
    expect(new Set(names).size).to.equal(names.length);
    expect(CONTENT_IGNORED.map((i) => i.collection).sort()).to.deep.equal(['crawlingManagerConfigs']);
    for (const i of CONTENT_IGNORED) expect(i.reason.length).to.be.greaterThan(20);
  });
  it('mapping files import no repository code', () => {
    const src = require('node:fs').readFileSync(require.resolve('../../../src/scripts/docstore-migrate/mapping/content'), 'utf8') as string;
    expect(src).to.not.match(/libs\/repositories/);
  });
});

for (const bin of PG_BINS.length ? PG_BINS : ['none']) {
  suite(`docstore-migrate: content, OAuth and platform data (${bin})`, function () {
    this.timeout(240_000);
    let stack: PgStack;
    const docs = contentDocs();
    // the Slack links continue chat sessions; those are another group's tables, so two sessions are planted directly
    const sessions = async (): Promise<void> => {
      await stack.admin(async (c) => {
        for (const [id, org] of [[SESSION_1, ORG_A], [SESSION_2, ORG_B]] as const)
          await c.query(
            "INSERT INTO conversation.chat_sessions (org_id, id, user_id, initiator, created_at, updated_at) VALUES ($1, $2, $3, $3, now(), now())",
            [String(org), String(id), String(USER_1)],
          );
      });
    };

    before(async () => {
      process.env.DOC_STORE = 'postgres';
      stack = await startPgStack(bin, 'migrate_content', { importer: true });
      await stack.admin((c) => c.query("SELECT platform.job_start('docstore_migration')"));    // the import functions of the install-wide tables run only inside a job
      await sessions();
    });
    after(async () => {
      await stack?.stop();
    });

    // (device authorizations and agent registrations are install-wide: the jobs login cannot read them back, so they have their own tests)
    const INSTALL_WIDE = ['oauthDeviceCodes', 'oauthAgentRegistrations'];
    const mine = (): Record<string, unknown[]> => Object.fromEntries(CONTENT_MAPPINGS.filter((m) => !INSTALL_WIDE.includes(m.collection)).map((m) => [m.collection, docs[m.collection] ?? []]));
    const count = async (t: string, where = 'true'): Promise<number> =>
      Number((await stack.admin((c) => c.query(`SELECT count(*)::int AS n FROM ${t} WHERE ${where}`))).rows[0].n);

    // the generic reader looks rows up with a text array, which a bigint key (the outbox) does not accept: it is read here
    const withoutOutbox = (): Record<string, unknown[]> => ({ ...mine(), outbox_events: [] });

    it('copies the outbox state and reads back identical documents', async () => {
      const m = CONTENT_MAPPINGS.find((x) => x.collection === 'outbox_events')!;
      const repo = REPOSITORIES[m.repository]!() as Importable;
      const neutralDocs = docs.outbox_events!.map((d) => toNeutral(d, new SourceReport(), m.collection));
      const st = await jobsUnit((tx) => repo.importBatch(m.collection, neutralDocs, tx));
      expect(st.inserted['platform.outbox']).to.equal(3);
      expect(st.skipped.map((x) => x.reason)).to.have.length(2);
      const rep = new SourceReport();
      const source = docs.outbox_events!.map((d) => prepareSource(m, d, rep)).filter((d): d is NonNullable<typeof d> => d !== null);
      const rows = await jobsUnit((_tx, k) => k.query<{ r: Record<string, unknown> }>('SELECT to_jsonb(t) AS r FROM platform.outbox t'));
      expect(compareDocs(source, rows.rows.map((x) => decodeDoc(m, x.r, {})))).to.deep.equal([]);
      expect(rep.applied.get('publishing becomes pending')).to.equal(1);
    });

    it('outbox: two events that fold to one table id stop the copy; the same event again is accepted', async () => {
      const m = CONTENT_MAPPINGS.find((x) => x.collection === 'outbox_events')!;
      const repo = REPOSITORIES[m.repository]!() as Importable;
      // same seconds and same low 21 bits of the counter, different ObjectIds
      const hexA = (1577836800 + 400_000_000).toString(16).padStart(8, '0') + 'abcdef0123' + '000005';
      const hexB = hexA.slice(0, 18) + '800005';
      const ev = (id: string, key: string): Record<string, unknown> => ({
        _id: id, topic: 't', key, value: JSON.stringify({ payload: { orgId: String(ORG_A) } }), createdAt: new Date('2026-01-02T00:00:00.123Z'), status: 'pending',
      });
      const run = (list: Record<string, unknown>[]) =>
        jobsUnit((tx) => repo.importBatch(m.collection, list.map((d) => toNeutral(d, new SourceReport(), m.collection)), tx));
      const folded = (): Promise<number> => count('platform.outbox', `id = ${String(Number(BigInt(parseInt(hexA.slice(0, 8), 16) - 1577836800) * 2097152n + 5n))}`);
      expect((await run([ev(hexA, 'a')])).inserted['platform.outbox']).to.equal(1);
      expect((await run([ev(hexA, 'a')])).inserted['platform.outbox'] ?? 0, 'an exact retry writes nothing and does not fail').to.equal(0);
      let err: Error | undefined;
      await run([ev(hexB, 'b')]).catch((e: Error) => (err = e));
      expect(err?.message, 'a different event on a held id').to.contain(hexB).and.to.contain('already holds');
      const hexC = hexA.slice(0, 8) + 'cccccccccc' + '000009';
      const hexD = hexC.slice(0, 18) + '800009';
      err = undefined;
      await run([ev(hexC, 'c'), ev(hexD, 'd')]).catch((e: Error) => (err = e));
      expect(err?.message, 'two different events in one batch').to.contain(hexC).and.to.contain(hexD);
      // the same id in another organisation is another event, not a collision
      const other = { ...ev(hexA, 'o'), value: JSON.stringify({ payload: { orgId: String(ORG_B) } }) };
      expect((await run([other])).inserted['platform.outbox']).to.equal(1);
      await stack.admin((c) => c.query("DELETE FROM platform.outbox WHERE msg_key = 'o'"));
      expect(await folded(), 'nothing was dropped or overwritten').to.equal(1);
      expect(await count('platform.outbox', "msg_key = 'a'")).to.equal(1);
      expect(await count('platform.outbox', "msg_key IN ('b','c','d')")).to.equal(0);
      await stack.admin((c) => c.query("DELETE FROM platform.outbox WHERE msg_key = 'a'"));   // the next test counts the fixture's rows
    });

    it('copies every other collection and reads back identical documents (zero diffs)', async () => {
      // the verifier reads a Slack link without orgId as holding its conversation's organisation (R45MIGR-FU-3)
      const r = await copyAndCompare(withoutOutbox(), MAPPINGS, { chatSessions: [{ _id: SESSION_1, orgId: ORG_A }, { _id: SESSION_2, orgId: ORG_B }] });
      // a custom mapping is compared by its own verifier (Slack account links: the test below)
      for (const m of CONTENT_MAPPINGS.filter((x) => x.collection !== 'outbox_events' && !INSTALL_WIDE.includes(x.collection) && !x.custom)) expect(r.diffs[m.collection], m.collection).to.deep.equal([]);
      // the listed transforms the report carries
      expect(r.report.skipped.get('mail log entry without an organisation (the table is per organisation)')).to.equal(1);
      expect(r.report.applied.get("absent orgId read as its conversation's organisation")).to.equal(2);
      expect([...r.report.nul.keys()].some((k) => k.startsWith('documents.documentName'))).to.equal(true);
    });

    it('pending device authorizations are copied through the vetted import function; one that has expired is not; a later copy brings the status up to date', async () => {
      const m = CONTENT_MAPPINGS.find((x) => x.collection === 'oauthDeviceCodes')!;
      const repo = REPOSITORIES[m.repository]!() as Importable;
      const neutral = docs.oauthDeviceCodes!.map((d) => toNeutral(d, new SourceReport(), m.collection));
      // with no organisation to hold them nothing is copied
      expect((await jobsUnit((tx) => repo.importBatch(m.collection, neutral, tx))).skipped).to.have.length(neutral.length);
      await stack.admin((c) => c.query("INSERT INTO identity.orgs (org_id, domain, contact_email, account_type, created_at, updated_at) VALUES ($1, 'a.example', 'o@a.example', 'business', now(), now())", [String(ORG_A)]));
      await jobsUnit((tx) => repo.importBatch(m.collection, neutral, tx));
      const rows = (await stack.admin((c) => c.query('SELECT user_code, status, org_id, scopes FROM oauth.device_authorizations ORDER BY user_code'))).rows;
      expect(rows.map((r: any) => [r.user_code, r.status])).to.deep.equal([['ABCD-EFGH', 'pending'], ['JKLM-NPQR', 'approved']]);
      expect(rows.every((r: any) => r.org_id === String(ORG_A))).to.equal(true);   // all of them sit in the install's organisation
      // the first was approved in MongoDB meanwhile
      const changed = neutral.map((d, i) => (i === 0 ? { ...d, status: 'approved', userId: d.userId ?? String(USER_1), orgId: String(ORG_A) } : d));
      await jobsUnit((tx) => withUpsert(['oauth.device_authorizations'], () => repo.importBatch(m.collection, changed, tx)));   // as the follower writes a changed document
      expect((await stack.admin((c) => c.query("SELECT status FROM oauth.device_authorizations WHERE user_code = 'ABCD-EFGH'"))).rows[0].status).to.equal('approved');
      expect(await count('oauth.device_authorizations')).to.equal(2);
    });

    it('agent registrations are copied into the install organisation; one past its retention is not; a later copy brings a revocation over', async () => {
      const m = CONTENT_MAPPINGS.find((x) => x.collection === 'oauthAgentRegistrations')!;
      const repo = REPOSITORIES[m.repository]!() as Importable;
      const neutral = docs.oauthAgentRegistrations!.map((d) => toNeutral(d, new SourceReport(), m.collection));
      // the install organisation exists from the device test above
      const st = await jobsUnit((tx) => repo.importBatch(m.collection, neutral, tx));
      expect(st.skipped, 'the one past its retention').to.have.length(1);
      const rows = (await stack.admin((c) => c.query('SELECT id, status, org_id, approved_scopes FROM oauth.agent_registrations ORDER BY id'))).rows;
      expect(rows.map((r: any) => r.status)).to.deep.equal(['consumed', 'pending']);
      expect(rows.every((r: any) => r.org_id === String(ORG_A))).to.equal(true);
      expect(rows[0].approved_scopes).to.deep.equal(['org:read', 'kb:read']);
      // the consumed one was revoked in MongoDB meanwhile
      const changed = neutral.map((d, i) => (i === 0 ? { ...d, status: 'revoked' } : d));
      await jobsUnit((tx) => withUpsert(['oauth.agent_registrations'], () => repo.importBatch(m.collection, changed, tx)));
      expect((await stack.admin((c) => c.query('SELECT status FROM oauth.agent_registrations WHERE id = $1', [rows[0].id]))).rows[0].status).to.equal('revoked');
      expect(await count('oauth.agent_registrations')).to.equal(2);
    });

    it('a second copy writes nothing', async () => {
      expect(await copyAgainInserts(mine(), MAPPINGS)).to.equal(0);
    });

    it('counts: what was copied, and what was left out, add up', async () => {
      expect(await count('content.documents')).to.equal(3);
      expect(await count('content.project_members')).to.equal(3);
      expect(await count('platform.notifications')).to.equal(3);
      expect(await count('platform.mail_log')).to.equal(2);
      expect(await count('oauth.access_tokens')).to.equal(3);
      expect(await count('integrations.slack_threads')).to.equal(2);
    });

    it('outbox: published history is not copied, publishing becomes pending, failed stays, ids keep creation order', async () => {
      const rows = (await stack.admin((c) => c.query('SELECT id::text, status, claimed_at, last_error, key_order FROM (SELECT o.*, row_number() OVER (ORDER BY created_at) AS key_order FROM platform.outbox o) x ORDER BY created_at'))).rows;
      expect(rows.map((r: { status: string }) => r.status)).to.deep.equal(['pending', 'pending', 'failed']);
      expect(rows.every((r: { claimed_at: unknown }) => r.claimed_at === null)).to.equal(true);
      const ids = rows.map((r: { id: string }) => Number(r.id));
      expect(ids).to.deep.equal([...ids].sort((a, b) => a - b));
      expect(rows[2].last_error).to.equal('broker down');
    });

    it('authorization codes are stored hashed and the plain code is nowhere', async () => {
      const rows = (await stack.admin((c) => c.query('SELECT code_hash FROM oauth.authorization_codes ORDER BY created_at'))).rows;
      expect(rows.map((r: { code_hash: string }) => r.code_hash).sort()).to.deep.equal([sha('plain-code-1'), sha('plain-code-2')].sort());
      const cols = (await stack.admin((c) => c.query("SELECT column_name FROM information_schema.columns WHERE table_schema = 'oauth' AND table_name = 'authorization_codes'"))).rows.map((r: { column_name: string }) => r.column_name);
      expect(cols).to.not.include('code');
    });

    it('notifications from months with no partition land in the default partition and read back', async () => {
      const r = (await stack.admin((c) => c.query("SELECT tableoid::regclass::text AS p FROM platform.notifications WHERE created_at < '2020-01-01'"))).rows;
      expect(r.map((x: { p: string }) => x.p)).to.deep.equal(['platform.notifications_def']);
    });

    it('connector_id follows the customMetadata entry', async () => {
      const r = (await stack.admin((c) => c.query('SELECT id, connector_id, initiator_unset, initiator_user_id FROM content.documents'))).rows as Array<Record<string, unknown>>;
      const by = new Map(r.map((x) => [x.id, x]));
      expect(by.get(String(ids.DOC_FULL))?.connector_id).to.equal('conn-42');
      expect(by.get(String(ids.DOC_NO_ARRAYS))?.initiator_unset).to.equal(true);
      expect(by.get(String(ids.DOC_NULL_INITIATOR))).to.include({ initiator_unset: false, initiator_user_id: null });
    });

    it('a field a mapper drops shows as a difference on exactly that document', async () => {
      await stack.admin((c) => c.query("UPDATE content.documents SET custom_metadata = '[]' WHERE id = $1", [String(ids.DOC_FULL)]));
      const m = CONTENT_MAPPINGS.find((x) => x.collection === 'documents')!;
      const rep = new SourceReport();
      const source = (docs.documents as unknown[]).map((d) => prepareSource(m, d, rep)!);
      const decoded = await jobsUnit((_tx, k) => readDecoded(k, m, source.map((d) => String(d._id))));
      const diffs = compareDocs(source, decoded);
      expect(diffs.map((d) => `${d.id} ${d.detail.join(';')}`)).to.have.length(1);
      expect(diffs[0]?.id).to.equal(String(ids.DOC_FULL));
      expect(diffs[0]?.detail.join(' ')).to.include('customMetadata');
    });

    it('a Slack link whose conversation was not copied is reported, not stamped with a guess', async () => {
      const repo = REPOSITORIES['slack-thread']!() as Importable;
      const orphan = toNeutral({ _id: oid(), threadId: 't-orphan', botId: 'B9', email: 'z@example.test', conversationId: oid(), createdAt: new Date(), updatedAt: new Date() }, new SourceReport(), 'slack_conversations');
      const st = await jobsUnit((tx) => repo.importBatch('slack_conversations', [orphan], tx));
      expect(st.skipped).to.have.length(1);
      expect(st.inserted['integrations.slack_threads']).to.equal(0);
    });

    it("R45-MIGR, R45MIGR-FU-3: a Slack link's orgId is its conversation's organisation: the same one is copied and verified as org_id, another one is refused", async () => {
      const org = (await stack.admin((c) => c.query('SELECT org_id FROM conversation.chat_sessions WHERE id = $1', [String(SESSION_1)]))).rows[0]?.org_id as string;
      expect(org, 'the fixture conversation was copied').to.be.a('string');
      const m = MAPPINGS.find((x) => x.collection === 'slack_conversations')!;
      const repo = REPOSITORIES[m.repository]!() as Importable;
      const link = (threadId: string, orgId: string) => ({ _id: oid(), threadId, botId: 'B7', email: 'o@example.test', conversationId: SESSION_1, orgId, createdAt: new Date('2026-11-01T10:00:00.000Z'), updatedAt: new Date('2026-11-01T10:00:00.000Z'), __v: 0 });
      const same = link('1700000000.000700', org);
      const other = link('1700000000.000800', 'f'.repeat(24));
      const st = await jobsUnit((tx) => repo.importBatch(m.collection, [same, other].map((d) => toNeutral(d, new SourceReport(), m.collection)), tx));
      expect(st.inserted['integrations.slack_threads']).to.equal(1);
      expect(st.skipped).to.deep.equal([{ id: String(other._id), reason: 'Slack link whose orgId names another organisation than its conversation' }]);
      const rep = new SourceReport();
      const src = prepareSource(m, same, rep)!;
      const decoded = await jobsUnit((_tx, k) => readDecoded(k, m, [String(src._id)]));
      expect(compareDocs([src], decoded)).to.deep.equal([]);
      // carried as org_id (a rollback writes it back), no longer dropped
      expect(src.orgId).to.equal(org);
      expect(decoded[0]?.orgId).to.equal(org);
      expect(rep.applied.get('dropped orgId')).to.equal(undefined);
    });

    it('Slack account links (R40SLACK-FU-1): each organisation keeps its own, a link to another organisation\'s person is reported, a second copy writes nothing, verify reads them back equal', async () => {
      await stack.admin(async (c) => {
        for (const [org, domain] of [[ORG_A, 'a.example'], [ORG_B, 'b.example']] as const)
          await c.query("INSERT INTO identity.orgs (org_id, domain, contact_email, account_type, created_at, updated_at) VALUES ($1, $2, 'o@' || $2, 'business', now(), now()) ON CONFLICT DO NOTHING", [String(org), domain]);
        for (const [org, user, slug] of [[ORG_A, USER_1, 'user-71'], [ORG_B, USER_2, 'user-72']] as const)
          await c.query("INSERT INTO identity.users (org_id, id, slug, email, role, is_deleted, created_at, updated_at) VALUES ($1, $2, $3, $3 || '@example.test', 'member', false, now(), now()) ON CONFLICT DO NOTHING", [String(org), String(user), slug]);
      });
      const m = MAPPINGS.find((x) => x.collection === 'slack_user_links')!;
      const repo = REPOSITORIES[m.repository]!() as Importable;
      const link = (over: Record<string, unknown>) => ({ _id: oid(), orgId: String(ORG_A), workspaceId: 'E1', slackUserId: 'U1', teamId: 'T1', userId: String(USER_1), email: 'a@example.test', slackName: 'ada', linkedAt: new Date('2026-11-01T10:00:00.000Z'), createdAt: new Date('2026-11-01T10:00:00.000Z'), updatedAt: new Date('2026-11-01T10:00:00.000Z'), __v: 0, ...over });
      const good = [link({}), link({ orgId: String(ORG_B), userId: String(USER_2), email: 'b@example.test' })];
      const foreign = link({ slackUserId: 'U9', userId: String(USER_2) });
      const neutral = [...good, foreign].map((d) => toNeutral(d, new SourceReport(), m.collection));
      const st = await jobsUnit((tx) => repo.importBatch(m.collection, neutral, tx));
      expect(st.inserted['integrations.slack_user_links']).to.equal(2);
      expect(st.skipped.map((x) => x.id)).to.deep.equal([String(foreign._id)]);
      expect((await jobsUnit((tx) => repo.importBatch(m.collection, neutral, tx))).inserted['integrations.slack_user_links']).to.equal(0);
      const rows = (await stack.admin((c) => c.query('SELECT to_jsonb(l) AS r FROM integrations.slack_user_links l ORDER BY org_id'))).rows.map((x: { r: Record<string, unknown> }) => x.r);
      expect(rows.map((r: Record<string, unknown>) => [r.org_id, r.user_id])).to.have.deep.members([[String(ORG_A), String(USER_1)], [String(ORG_B), String(USER_2)]]);
      expect(compareSlackLinks(m, good, rows).diffs).to.deep.equal([]);
      // the follower replaces a link in place when MongoDB changes it
      const renamed = neutral.slice(0, 1).map((d) => ({ ...d, slackName: 'ada l.' }));
      await jobsUnit((tx) => withUpsert(['integrations.slack_user_links'], () => repo.importBatch(m.collection, renamed, tx)));
      expect((await stack.admin((c) => c.query('SELECT slack_name FROM integrations.slack_user_links WHERE org_id = $1', [String(ORG_A)]))).rows[0].slack_name).to.equal('ada l.');
    });
  });
}
