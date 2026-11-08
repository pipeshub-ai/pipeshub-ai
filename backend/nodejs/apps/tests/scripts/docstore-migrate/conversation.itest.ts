import 'reflect-metadata';
import { expect } from 'chai';
import { MAPPINGS } from '../../../src/scripts/docstore-migrate/mapping';
import { CONVERSATION_INVARIANTS, CONVERSATION_MAPPINGS } from '../../../src/scripts/docstore-migrate/mapping/conversation';
import { PG_BINS, PgStack, haveStack, startPgStack } from '../../stores/pg-store-harness';
import { copyAgainInserts, copyAndCompare, jobsUnit } from './roundtrip';
import { conversationDocs, C1, S1, S3, ORG1 } from './fixtures/conversation';

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
      expect(r.diffs.chatSessions.map((d) => d.id)).to.deep.equal([String(S1)]);
      expect(r.diffs.chatSessions[0]!.kind).to.equal('differs');
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
