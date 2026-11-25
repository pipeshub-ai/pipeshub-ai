/**
 * R48MIGFIX-1: a run with no confidence is saved with `confidence: null` (the durable projection, and the legacy chat before it: the
 * recorded legacy transcripts hold the same null), while the migration mapping read a stored null as "absent" and its census refused
 * one, so preflight was red on every install that had chatted. The app's message shape stays as main writes it; the mapping accepts
 * the null. These tests hold the two sides together:
 *
 *  - every answer message the app builds, whatever outcome the run ended with, passes the census of `chatSessionMessages`, and
 *    the verifier compares its null as absent (the column holds NULL for both, and every reader treats them alike);
 *  - every path a mapping accepts a stored null at (`nullOk`) is mapped and is actually read as absent, so a new `nullOk` entry
 *    without its transform fails here rather than as a difference in a customer's verify.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import type { JournalEvent } from '../../../src/modules/agent-harness/domain/journal';
import { foldTimeline } from '../../../src/modules/agent-harness/projection/timeline';
import { buildCompletion } from '../../../src/modules/agent-harness/projection/transcript';
import { buildAIResponseMessage } from '../../../src/modules/enterprise_search/utils/utils';
import type { IAIResponse } from '../../../src/modules/enterprise_search/types/conversation.interfaces';
import { Census, judgeCensus } from '../../../src/scripts/docstore-migrate/census';
import { MAPPINGS } from '../../../src/scripts/docstore-migrate/mapping';
import { SourceReport, prepareSource } from '../../../src/scripts/docstore-migrate/source';

const RUN = 'run-r48';
const messages = MAPPINGS.find((m) => m.collection === 'chatSessionMessages')!;

function journal(answer: Record<string, unknown>): JournalEvent[] {
  const specs: Array<[string, Record<string, unknown>, 'node' | 'worker']> = [
    ['run.admitted', { runKind: 'interactive', principalRef: 'p_user' }, 'node'],
    ['run.claimed', { attempt: 1, fence: 1, leaseMs: 60000 }, 'node'],
    ['x.pipeshub.answer.finalized', answer, 'worker'],
    ['run.completed', { outcome: String(answer['outcome']) }, 'node'],
  ];
  return specs.map(
    ([type, data, writer], index) =>
      ({
        orgId: 'org-1', sessionId: 'ses-1', streamId: 'ses-1', seq: index + 1, runId: RUN,
        attemptId: writer === 'worker' ? `${RUN}.a1` : undefined, fence: writer === 'worker' ? 1 : undefined,
        type, schemaVersion: 1, critical: !type.startsWith('x.'), ts: '2026-11-14T12:00:00.000Z', writer,
        visibility: 'owner', data,
      }) as JournalEvent,
  );
}

/** The message the conversation writers store for a run that finalized `answer` (both stores build it with buildAIResponseMessage). */
function storedMessage(answer: Record<string, unknown>): Record<string, unknown> {
  const completion = buildCompletion(foldTimeline(journal(answer), RUN));
  expect(completion, 'the run has a completion').to.not.equal(null);
  const built = buildAIResponseMessage({ statusCode: 200, data: { ...completion, citations: [] } as unknown as IAIResponse });
  // what MongoDB keeps: undefined keys are not stored
  return JSON.parse(JSON.stringify({ ...built, orgId: 'a'.repeat(24), sessionId: 'b'.repeat(24), seq: 2, __v: 0 }));
}

const base = { answer: 'Done.', partTexts: ['Done.'], citations: [], confidence: null };

describe('R48MIGFIX-1: stored nulls and the migration census', () => {
  for (const [name, answer] of [
    ['answered', { outcome: 'answered', ...base }],
    ['answered with a match type', { outcome: 'answered', ...base, answerMatchType: 'Derived From Blocks' }],
    ['needs input', { outcome: 'needs_input', ...base, answer: 'Which region?', partTexts: ['Which region?'] }],
    ['error', { outcome: 'error', ...base, answer: 'It failed.', partTexts: ['It failed.'], errorCode: 'engine_failed' }],
  ] as const)
    it(`a ${name} run with no confidence passes the census and its null is compared as absent`, () => {
      const doc = storedMessage(answer);
      const c = new Census();
      c.add(doc as never);
      const nulls = judgeCensus(messages, c).filter((f) => f.kind === 'null_in_omit_field');
      expect(nulls, JSON.stringify(nulls)).to.deep.equal([]);
      const prepared = prepareSource(messages, doc, new SourceReport())!;
      expect(prepared).to.not.have.property('confidence');
    });

  it('the app does store the null (the case the census must accept), and a confidence the run gave as given', () => {
    expect(storedMessage({ outcome: 'answered', ...base })).to.have.property('confidence', null);
    expect(storedMessage({ outcome: 'answered', ...base, confidence: 'High' }).confidence).to.equal('High');
  });

  it('every nullOk path of every mapping is a mapped field that a listed transform reads as absent', () => {
    const checked: string[] = [];
    for (const m of MAPPINGS)
      for (const path of m.nullOk ?? []) {
        expect(m.fields.map((f) => f.path), `${m.collection}.${path} is mapped`).to.include(path);
        const top = path.split('.')[0]!;
        const prepared = prepareSource(m, { _id: 'c'.repeat(24), [top]: null }, new SourceReport());
        if (prepared) expect(prepared, `${m.collection}.${path}: a stored null is read as absent`).to.not.have.property(top);
        checked.push(`${m.collection}.${path}`);
      }
    expect(checked).to.include('chatSessionMessages.confidence');
  });
});
