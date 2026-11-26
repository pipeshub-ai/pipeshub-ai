import { expect } from 'chai';
import {
  admittedJournalAppend,
} from '../../../src/modules/agent-harness/domain/execution';
import { inlineContentRef } from '../../../src/modules/agent-harness/domain/run-content';
import {
  ConversationHistory,
  EMPTY_LEDGER,
  extendLedger,
  HistoryMessage,
  planSessionImport,
  sessionImportedEvent,
} from '../../../src/modules/agent-harness/domain/session-import';
import { canonicalJsonBytes } from '../../../src/modules/agent-harness/contracts/canonical-json';
import { SessionImportContent } from '../../../src/modules/agent-harness/application/session-import-content';
import { InMemoryPayloadStore } from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { HarnessConflictError } from '../../../src/modules/agent-harness/domain/errors';
import { CONTENT_MAX_BYTES } from '../../../src/modules/agent-harness/domain/run-content';
import { dataKeyRing } from './support/data-protection';

/**
 * The import plan (P1b T3, MG-1/MG-2/MG-4): what a durable admission journals
 * as `session.imported`, from the conversation and the harness's ledger of it.
 */

const user = (seq: number, content: string): HistoryMessage => ({
  seq,
  turns: [{ role: 'user_query', content }],
});
const bot = (seq: number, content: string): HistoryMessage => ({
  seq,
  turns: [{ role: 'bot_response', content }],
});
const errorRow = (seq: number): HistoryMessage => ({ seq, turns: [] });

const legacy = [user(1, 'q1'), bot(2, 'a1'), user(3, 'q2'), bot(4, 'a2')];
/** Messages with no known provenance (these fixtures name none): the import is unknown context (UX2). */
const UNKNOWN = { complete: false, sources: [] };

const history = (
  messages: HistoryMessage[],
  ledger: ConversationHistory['ledger'] = null,
): ConversationHistory => ({
  messages,
  lastSeq: messages.reduce((max, m) => Math.max(max, m.seq), 0),
  ledger,
});

/** The ledger a conversation of `messages` has once the harness accounted for it all. */
const accounted = (messages: HistoryMessage[]) =>
  extendLedger(EMPTY_LEDGER, messages, history(messages).lastSeq);

describe('session import plan (P1b T3)', () => {
  it('MG-1: a legacy session imports every legacy turn, once, and accounts for them', () => {
    const { plan, ledger } = planSessionImport(history(legacy), false);
    expect(plan).to.deep.equal({
      upToMessageSeq: 4,
      turns: legacy.flatMap((m) => m.turns),
      replacesHistory: false,
      label: UNKNOWN,
    });
    expect(ledger).to.deep.equal(accounted(legacy));
    // The next admission, with nothing new: no import.
    expect(planSessionImport(history(legacy, ledger), true).plan).to.equal(null);
  });

  it('MG-1: a session with no model-visible message imports nothing', () => {
    expect(planSessionImport(history([]), false).plan).to.equal(null);
    expect(planSessionImport(history([errorRow(1)]), false).plan).to.equal(null);
  });

  it('MG-4: turns written after the ledger are imported alone (a delta), placed where the run starts', () => {
    const before = [...legacy, user(5, 'durable q'), bot(6, 'durable a')];
    const ledger = accounted(before);
    const meanwhile = [...before, user(7, 'legacy q'), bot(8, 'legacy a')];
    const { plan, ledger: after } = planSessionImport(history(meanwhile, ledger), true);
    expect(plan).to.deep.equal({
      upToMessageSeq: 8,
      turns: [
        { role: 'user_query', content: 'legacy q' },
        { role: 'bot_response', content: 'legacy a' },
      ],
      replacesHistory: false,
      label: UNKNOWN,
    });
    expect(after).to.deep.equal(accounted(meanwhile));
  });

  it('a row history leaves out (an error, a question card) advances the ledger and changes nothing', () => {
    const withError = [...legacy, errorRow(5)];
    const ledger = accounted(withError);
    expect(ledger.digest).to.equal(accounted(legacy).digest);
    expect(ledger.seq).to.equal(5);
    expect(planSessionImport(history(withError, ledger), true).plan).to.equal(null);
  });

  it('MG-2c: a covered message that changed (a regenerated answer) re-imports the whole conversation', () => {
    const ledger = accounted(legacy);
    const regenerated = [...legacy.slice(0, 3), bot(4, 'a2, regenerated')];
    const { plan } = planSessionImport(history(regenerated, ledger), true);
    expect(plan).to.deep.equal({
      upToMessageSeq: 4,
      turns: regenerated.flatMap((m) => m.turns),
      replacesHistory: true,
      mismatch: 'changed',
      label: UNKNOWN,
    });
  });

  it('MG-2c: a covered message that is gone re-imports the whole conversation', () => {
    const ledger = accounted(legacy);
    const { plan } = planSessionImport(
      { messages: legacy.slice(0, 2), lastSeq: 4, ledger },
      true,
    );
    expect(plan?.mismatch).to.equal('changed');
    expect(plan?.turns).to.deep.equal(legacy.slice(0, 2).flatMap((m) => m.turns));
  });

  it('MG-2c: a covered message changed and new turns after it: the whole conversation, once', () => {
    const ledger = accounted(legacy);
    const now = [user(1, 'q1 edited'), ...legacy.slice(1), user(5, 'q3'), bot(6, 'a3')];
    const { plan } = planSessionImport(history(now, ledger), true);
    expect(plan?.replacesHistory).to.equal(true);
    expect(plan?.turns).to.deep.equal(now.flatMap((m) => m.turns));
  });

  it('MG-2d: a ledger another writer left behind (interleaved) re-imports the whole conversation', () => {
    const ledger = { ...accounted(legacy), diverged: true };
    const { plan } = planSessionImport(history(legacy, ledger), true);
    expect(plan?.mismatch).to.equal('interleaved');
    expect(plan?.replacesHistory).to.equal(true);
  });

  it('MG-2d: a session that ran durably before there was a ledger (untracked) re-imports the whole conversation', () => {
    const { plan } = planSessionImport(history(legacy), true);
    expect(plan).to.deep.equal({
      upToMessageSeq: 4,
      turns: legacy.flatMap((m) => m.turns),
      replacesHistory: true,
      mismatch: 'untracked',
      label: UNKNOWN,
    });
  });

  it('the ledger chains in seq order: the same messages in another order or content differ', () => {
    const a = accounted(legacy);
    expect(accounted(legacy).digest).to.equal(a.digest);
    expect(accounted([legacy[1]!, legacy[0]!, ...legacy.slice(2)].map((m, i) => ({ ...m, seq: i + 1 }))).digest).to.not.equal(a.digest);
    expect(extendLedger(accounted(legacy.slice(0, 2)), legacy, 4)).to.deep.equal(a);
  });

  it('the event is what the catalog accepts, before run.admitted; its digest is the transcript hash', () => {
    const { plan } = planSessionImport(history(legacy), false);
    const ref = inlineContentRef(canonicalJsonBytes(plan!.turns));
    const event = sessionImportedEvent(plan!, ref);
    const append = admittedJournalAppend(
      { orgId: 'org_1', sessionId: 'ses_1', runId: 'run_1', actorId: 'user_1' },
      'session',
      event,
    );
    expect(append.events.map((e) => e.type)).to.deep.equal(['session.imported', 'run.admitted']);
    expect(append.events[0]!.data).to.deep.equal({
      upToMessageSeq: 4,
      transcriptDigest: ref.hash,
      transcriptRef: ref,
      messages: 4,
    });
    const replacing = sessionImportedEvent({ ...plan!, replacesHistory: true }, ref);
    expect(replacing.data['replacesHistory']).to.equal(true);
    expect(
      JSON.parse(Buffer.from(ref.ref.slice('inline:'.length), 'base64url').toString('utf8')),
    ).to.deep.equal(plan!.turns);
  });

  describe('where the transcript is kept (MG-1c)', () => {
    const run = { orgId: 'org_1', sessionId: 'ses_1', runId: 'run_1' };
    const planOf = (bytes: number) => ({
      upToMessageSeq: 2,
      turns: [{ role: 'bot_response', content: 'x'.repeat(bytes) }],
      replacesHistory: false,
      label: UNKNOWN,
    });

    it('in its event up to the inline limit; sealed content above it, hashed over the plaintext', async () => {
      const payloads = new InMemoryPayloadStore();
      const content = new SessionImportContent(dataKeyRing().cipher, payloads, 1024);
      const small = await content.store(run, planOf(100));
      expect(small.sealed).to.equal(false);
      expect(small.ref).to.deep.equal(inlineContentRef(canonicalJsonBytes(planOf(100).turns)));
      const large = await content.store(run, planOf(4096));
      expect(large.sealed).to.equal(true);
      expect(large.ref.ref.startsWith('inline:')).to.equal(false);
      expect(large.ref.hash).to.equal(inlineContentRef(canonicalJsonBytes(planOf(4096).turns)).hash);
      expect(payloads.objects.size).to.equal(1);
    });

    it('a transcript over the content limit is refused (SESSION_HISTORY_TOO_LARGE), nothing stored', async () => {
      const payloads = new InMemoryPayloadStore();
      const content = new SessionImportContent(dataKeyRing().cipher, payloads);
      let refused: unknown = null;
      try {
        await content.store(run, planOf(CONTENT_MAX_BYTES));
      } catch (error) {
        refused = error;
      }
      expect((refused as HarnessConflictError).code).to.equal('SESSION_HISTORY_TOO_LARGE');
      expect(payloads.objects.size).to.equal(0);
    });
  });
});
