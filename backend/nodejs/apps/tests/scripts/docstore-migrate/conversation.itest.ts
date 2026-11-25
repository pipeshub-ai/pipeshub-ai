import 'reflect-metadata';
import { expect } from 'chai';
import { MAPPINGS } from '../../../src/scripts/docstore-migrate/mapping';
import { CONVERSATION_INVARIANTS, CONVERSATION_MAPPINGS } from '../../../src/scripts/docstore-migrate/mapping/conversation';
import { PG_BINS, PgStack, haveStack, startPgStack } from '../../stores/pg-store-harness';
import { copyAgainInserts, copyAndCompare, jobsUnit } from './roundtrip';
import { conversationDocs, C1, S1, S3, ORG1, U1 } from './fixtures/conversation';
import { at, oid } from './roundtrip';

const suite = haveStack ? describe : describe.skip;
const count = async (stack: PgStack, table: string): Promise<number> =>
  Number((await stack.admin((c) => c.query(`SELECT count(*) AS n FROM ${table}`))).rows[0].n);

for (const bin of PG_BINS.length ? PG_BINS : ['none']) {
  suite(`docstore-migrate: conversation collections (${bin})`, function () {
    this.timeout(180_000);
    let stack: PgStack;
    const mappings = MAPPINGS.filter((m) => CONVERSATION_MAPPINGS.includes(m));
    before(async () => {
      process.env.DOC_STORE = 'postgres';
      stack = await startPgStack(bin, 'migrate_conv', { importer: true });
    });
    after(async () => {
      await stack?.stop();
    });

    it('copies every collection and reads back identical documents; a second copy writes nothing', async () => {
      const docs = conversationDocs();
      const r = await copyAndCompare(docs, mappings);
      // the message of no session and the citation without an organisation are not copied, so they are not compared either
      for (const [name, d] of Object.entries(r.diffs)) {
        const stray = d.filter((x) => x.kind === 'missing_in_pg').map((x) => x.id);
        const expected = name === 'chatSessionMessages' ? 1 : name === 'citations' ? 1 : 0;
        expect(stray.length, `${name} missing: ${stray.join(',')}`).to.equal(expected);
        expect(d.filter((x) => x.kind !== 'missing_in_pg'), name).to.deep.equal([]);
      }
      expect(await count(stack, 'conversation.chat_messages')).to.equal(5);
      expect(await count(stack, 'conversation.chat_session_members')).to.equal(2); // S1: two users (U2 once)
      expect(await count(stack, 'conversation.citations')).to.equal(2);
      expect(await copyAgainInserts(docs, mappings)).to.equal(0);
    });

    it('a pinned chat stays pinned, a stored false is unset, and the fork link in metadata travels', async () => {
      const rows = (await stack.admin((c) => c.query('SELECT id, is_pinned, metadata FROM conversation.chat_sessions'))).rows as Array<{ id: string; is_pinned: boolean; metadata: Record<string, unknown> | null }>;
      expect(rows.filter((r) => r.is_pinned).map((r) => r.id)).to.deep.equal([String(S1)]);
      const forked = rows.find((r) => r.id === String(S3));
      expect(forked?.metadata).to.deep.equal({ forkedFromConversationId: String(S1), forkedFromMessageId: String(C1) });
    });

    it('a field the importer would drop shows up as a diff on exactly the affected session', async () => {
      await stack.admin((c) => c.query("UPDATE conversation.chat_sessions SET metadata = metadata - 'k' WHERE id = $1", [String(S1)]));
      const r = await copyAndCompare({ chatSessions: conversationDocs().chatSessions }, mappings);
      expect(r.diffs.chatSessions!.map((d) => d.id)).to.deep.equal([String(S1)]);
      expect(r.diffs.chatSessions![0]!.kind).to.equal('differs');
    });

    it('R45-MIGR: the harness fields of a session and of a message are copied and read back equal; a dropped one is a diff', async () => {
      const sid = oid();
      const session = {
        _id: sid, sessionType: 'chat', nextSeq: 2, userId: U1, orgId: ORG1, initiator: U1, lastActivityAt: 1772359200000,
        harnessVersion: 'durable-v1', harnessLedger: { seq: 2, digest: 'd2', diverged: true }, erasureRequestedAt: 1772359300000,
        harnessSeen: { [String(U1)]: 1772359400000 },
        createdAt: at('2026-03-04T09:59:00.000Z'), updatedAt: at('2026-03-04T10:00:00.000Z'), __v: 0,
      };
      const base = { sessionId: sid, orgId: ORG1, contentFormat: 'MARKDOWN', citations: [], followUpQuestions: [], feedback: [], modelInfo: { chatMode: 'quick' }, appliedFilters: { apps: [], kb: [], recordGroups: [], records: [], recordsExact: [] }, attachments: [], referenceData: [], tools: [], reasoning: [], parts: [], createdAt: at('2026-03-04T10:00:00.000Z'), updatedAt: at('2026-03-04T10:00:00.000Z') };
      const answer = {
        ...base, _id: oid(), seq: 1, messageType: 'bot_response', content: 'answer', senderId: U1, runId: 'run-9', errorCode: 'mcp_tool_withdrawn', errorServers: ['Jira'],
        errorStop: { scope: 'org', reason: 'maintenance' }, truncatedBy: 'budget', budgetScope: 'pool-a', runOutcome: { kind: 'failed', closing: 'stopped' },
        notFound: { subject: 'Q3 plan', hits: 0, line: 'I could not find the Q3 plan.' }, limits: { items: [{ kind: 'clone' }], line: 'I could not clone it.' },
        copied: true, foreignTurn: true, label: { complete: true, sources: [{ kind: 'record', recordId: 'r-1' }] },
      };
      const asked = { ...base, _id: oid(), seq: 2, messageType: 'user_query', content: '/review', command: { name: 'review', version: '1', contentDigest: 'c', typed: '/review', owner: 'builtin' } };
      const r = await copyAndCompare({ chatSessions: [session], chatSessionMessages: [answer, asked] }, mappings);
      expect(r.diffs).to.deep.equal({ chatSessions: [], chatSessionMessages: [] });
      // the command without args is read as args {} (the schema default), counted
      expect(r.report.applied.get('command without args read as args {} (the schema default)')).to.equal(1);
      const row = (await stack.admin((c) => c.query('SELECT harness, sender_id, label FROM conversation.chat_messages WHERE id = $1', [String(answer._id)]))).rows[0];
      expect(row.sender_id).to.equal(String(U1));
      expect(row.harness).to.deep.include({ runId: 'run-9', notFound: answer.notFound, limits: answer.limits });
      // a field the importer would lose is a difference on exactly that message
      await stack.admin((c) => c.query("UPDATE conversation.chat_messages SET harness = harness - 'notFound' WHERE id = $1", [String(answer._id)]));
      await stack.admin((c) => c.query('UPDATE conversation.chat_sessions SET harness_seen = NULL WHERE id = $1', [String(sid)]));
      const again = await copyAndCompare({ chatSessions: [session], chatSessionMessages: [answer] }, mappings);
      expect(again.diffs.chatSessionMessages!.map((d) => d.id)).to.deep.equal([String(answer._id)]);
      expect(again.diffs.chatSessions!.map((d) => d.id)).to.deep.equal([String(sid)]);
    });

    it('the planted organisation mismatch is found', async () => {
      // stamp citation C1 with another organisation: the invariant names it
      await stack.admin((c) => c.query("UPDATE conversation.citations SET org_id = $2 WHERE id = $1", [String(C1), 'f'.repeat(24)]));
      const bad = await jobsUnit(async (_tx, k) => (await k.query(`/* pipeshub:test.inv */ ${CONVERSATION_INVARIANTS[0]!.sql}`)).rows);
      expect(bad.map((x) => (x as { id: string }).id)).to.include(String(C1));
      ORG1;
    });
  });
}
