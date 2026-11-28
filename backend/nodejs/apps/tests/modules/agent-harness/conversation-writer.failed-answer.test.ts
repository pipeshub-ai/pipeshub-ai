import 'reflect-metadata';
import { expect } from 'chai';
import { PgHarnessConversationWriter } from '../../../src/libs/repositories/agent-harness/conversation-writer/pg';
import { ConversationRepository, setConversationRepository } from '../../../src/libs/repositories/conversation';
import type { Tx } from '../../../src/libs/repositories/uow';

/**
 * Try again on a failed answer, as the PostgreSQL writer answers a regenerate: the error row a failed run left stands
 * in its answer's place, so a regenerate may name it, and when it is the newest answer its turn is taken back and its
 * question asked again, as today's loop redoes it. The writer's rule runs here over a repository that holds the rows in
 * memory; `conversation-writer.pg.itest.ts` runs the same on PostgreSQL.
 */

const ORG = 'a'.repeat(24);
const SESSION = 'b'.repeat(24);
const TX = {} as Tx;
const ref = { orgId: ORG, sessionId: SESSION };
const id = (n: number): string => n.toString(16).padStart(24, '0');

interface Row {
  _id: string;
  seq: number;
  messageType: string;
  content: string;
  modelInfo?: { chatMode: string };
  attachments?: unknown[];
}

const question = (n: number, content: string): Row => ({
  _id: id(n),
  seq: n,
  messageType: 'user_query',
  content,
  modelInfo: { chatMode: 'quick' },
  attachments: [],
});
const row = (n: number, messageType: 'bot_response' | 'error', content: string): Row => ({
  _id: id(n),
  seq: n,
  messageType,
  content,
});

/** The chat's rows, held as the repository would hold them; the writer reads and deletes through it. */
const chat = (rows: Row[]): Row[] => {
  const held = [...rows];
  const repository = {
    findMessage: async (sessionId: string, orgId: string, messageId: string) =>
      sessionId === SESSION && orgId === ORG ? (held.find((r) => r._id === messageId) ?? null) : null,
    listMessages: async (sessionId: string, q: { orgId: string; limit?: number; order?: 'asc' | 'desc' }) => {
      const mine = sessionId === SESSION && q.orgId === ORG ? [...held] : [];
      mine.sort((a, b) => (q.order === 'desc' ? b.seq - a.seq : a.seq - b.seq));
      return mine.slice(0, q.limit ?? mine.length).map((dto) => ({ dto }));
    },
    deleteMessages: async (ids: string[]) => {
      const before = held.length;
      held.splice(0, held.length, ...held.filter((r) => !ids.includes(r._id)));
      return before - held.length;
    },
  };
  setConversationRepository(repository as unknown as ConversationRepository);
  return held;
};

const FAILED = "The AI model's provider isn't responding right now. Please try again in a minute.";

describe('Try again on a failed answer, as the PostgreSQL conversation writer takes it', () => {
  afterEach(() => setConversationRepository());
  const writer = new PgHarnessConversationWriter();

  it("a failed run's error row is an answer a regenerate may name; a question is not", async () => {
    chat([question(1, 'What is the leave policy?'), row(2, 'bot_response', 'Twenty-five days.'), question(3, 'And for contractors?'), row(4, 'error', FAILED)]);
    expect(await writer.hasAnswer(ref, id(4), TX), 'the error row').to.equal(true);
    expect(await writer.hasAnswer(ref, id(2), TX), 'an answer').to.equal(true);
    expect(await writer.hasAnswer(ref, id(3), TX), 'a question').to.equal(false);
  });

  it('the newest answer being a failure, its turn is taken back and its question is asked again', async () => {
    const held = chat([
      question(1, 'What is the leave policy?'),
      row(2, 'bot_response', 'Twenty-five days.'),
      question(3, 'And for contractors?'),
      row(4, 'error', FAILED),
    ]);
    const asked = await writer.retractLastAnswer(ref, id(4), TX);
    expect(asked).to.deep.include({ query: 'And for contractors?', chatMode: 'quick' });
    expect(held.map((r) => r.content)).to.deep.equal(['What is the leave policy?', 'Twenty-five days.']);
  });

  it('a failure a later answer followed is not the last answer: nothing is taken back', async () => {
    const held = chat([
      question(1, 'What is the leave policy?'),
      row(2, 'error', FAILED),
      question(3, 'And for contractors?'),
      row(4, 'bot_response', 'Ten days.'),
    ]);
    expect(await writer.retractLastAnswer(ref, id(2), TX)).to.equal(null);
    expect(held).to.have.length(4);
  });
});
