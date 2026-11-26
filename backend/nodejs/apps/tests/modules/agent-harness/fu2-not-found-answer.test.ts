/**
 * ANSWER-CONTRACT-FU-2 (ADR-RUN-03 D4): the answer to a request whose subject was not found carries a typed
 * `notFound` on `x.pipeshub.answer.finalized` (the subject, how many records the searches showed, the closest
 * records' names when the person could not be asked, and the English `line` the answer starts with). The completion,
 * its final part and the saved message carry it, so the web chat words it in the reader's language and leaves the
 * English out; another viewer of a shared chat never gets the closest records' names, which came from the owner's
 * own search. A surface with no UI keeps the English line in the answer.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { JournalEvent, validateJournalAppend } from '../../../src/modules/agent-harness/domain/journal';
import { sharedCompletion } from '../../../src/modules/agent-harness/projection/audiences';
import { foldTimeline } from '../../../src/modules/agent-harness/projection/timeline';
import { buildCompletion, buildParts } from '../../../src/modules/agent-harness/projection/transcript';
import { buildAIResponseMessage } from '../../../src/modules/enterprise_search/utils/utils';
import { IAIResponse } from '../../../src/modules/enterprise_search/types/conversation.interfaces';
import { without } from './support/without';

const RUN = 'run-fu2';
const LINE = '**I couldn\'t find anything about “SAAS launch” in your knowledge.** What follows draws on 2 related documents that do not mention it, so check it before you rely on it.';
const ANSWER = `${LINE}\n\nA preliminary risk report.`;
const NOT_FOUND = { subject: 'SAAS launch', hits: 2, line: LINE, near: ['Launch checklist', 'On-call runbook'] };

function journal(answer: Record<string, unknown>): JournalEvent[] {
  const specs: Array<[string, Record<string, unknown>, 'node' | 'worker']> = [
    ['run.admitted', { runKind: 'scheduled', principalRef: 'p_user' }, 'node'],
    ['run.claimed', { attempt: 1, fence: 1, leaseMs: 60000 }, 'node'],
    ['x.pipeshub.answer.finalized', answer, 'worker'],
    ['run.completed', { outcome: String(answer['outcome']) }, 'node'],
  ];
  return specs.map(
    ([type, data, writer], index) =>
      ({
        orgId: 'org-1', sessionId: 'ses-1', streamId: 'ses-1', seq: index + 1, runId: RUN,
        attemptId: writer === 'worker' ? `${RUN}.a1` : undefined, fence: writer === 'worker' ? 1 : undefined,
        type, schemaVersion: 1, critical: !type.startsWith('x.'), ts: '2026-11-11T12:00:00.000Z', writer,
        visibility: 'owner', data,
      }) as JournalEvent,
  );
}

const answered = (extra: Record<string, unknown> = {}) => ({
  outcome: 'answered', answer: ANSWER, partTexts: [ANSWER], citations: [], confidence: 'Low', ...extra,
});
const appended = (data: Record<string, unknown>) => () =>
  validateJournalAppend({
    orgId: 'org-1', sessionId: 'ses-1', streamId: 'ses-1', runId: RUN, writer: 'worker',
    events: [{ type: 'x.pipeshub.answer.finalized', schemaVersion: 1, critical: false, visibility: 'owner', data }],
  } as never);
const VIEWS = {
  access: { citations: (c: unknown) => c, messages: (m: unknown) => m },
  artifacts: { card: (c: unknown) => c, text: (t: unknown) => t, messages: (m: unknown) => m, completion: (c: Record<string, unknown>) => c },
};

describe('ANSWER-CONTRACT-FU-2: the not-found fact travels typed, beside its English line', () => {
  it('the contract takes a whole not-found fact and refuses a malformed one', () => {
    expect(appended(answered({ notFound: NOT_FOUND }))).to.not.throw();
    expect(appended(answered({ notFound: { subject: 'SAAS launch', hits: 2, line: LINE, proceeded: true } }))).to.not.throw();
    expect(appended(answered({ notFound: { subject: 'SAAS launch', line: LINE } }))).to.throw();
    expect(appended(answered({ notFound: { ...NOT_FOUND, near: ['a', 'b', 'c', 'd'] } }))).to.throw();
    expect(appended(answered({ notFound: { ...NOT_FOUND, extra: 1 } }))).to.throw();
  });

  it('the completion, its final part and the saved message carry it; the answer keeps the English line', () => {
    const timeline = foldTimeline(journal(answered({ notFound: NOT_FOUND })), RUN);
    const completion = buildCompletion(timeline)!;
    expect(completion['notFound']).to.deep.equal(NOT_FOUND);
    expect(String(completion['answer']).startsWith(LINE)).to.equal(true);
    expect(buildParts(timeline).find((part) => part['isFinal'] === true)?.['notFound']).to.deep.equal(NOT_FOUND);
    const saved = buildAIResponseMessage({ statusCode: 200, data: { ...completion, citations: [] } as unknown as IAIResponse });
    expect(saved).to.deep.include({ notFound: NOT_FOUND });
    expect(saved.content.startsWith(LINE)).to.equal(true);
  });

  it("another viewer of a shared chat gets the fact without the closest records' names", () => {
    const timeline = foldTimeline(journal(answered({ notFound: NOT_FOUND })), RUN);
    const seen = sharedCompletion({ ...buildCompletion(timeline)!, final: true }, VIEWS.access as never, VIEWS.artifacts as never);
    const shared = without(NOT_FOUND, 'near');
    expect(seen['notFound']).to.deep.equal(shared);
    const finalPart = (seen['parts'] as Array<Record<string, unknown>>).find((part) => part['isFinal'] === true);
    expect(finalPart?.['notFound']).to.deep.equal(shared);
  });

  it('ANSWER-CONTRACT-SOURCES: the places looked in travel with the fact, to every viewer', () => {
    const places = { ...NOT_FOUND, places: ['Atlassian Rovo'] };
    expect(appended(answered({ notFound: places }))).to.not.throw();
    expect(appended(answered({ notFound: { ...places, places: ['a', 'b', 'c', 'd', 'e'] } }))).to.throw();
    const completion = buildCompletion(foldTimeline(journal(answered({ notFound: places })), RUN))!;
    expect((completion['notFound'] as { places?: string[] }).places).to.deep.equal(['Atlassian Rovo']);
    const seen = sharedCompletion({ ...completion, final: true }, VIEWS.access as never, VIEWS.artifacts as never);
    expect((seen['notFound'] as { places?: string[] }).places).to.deep.equal(['Atlassian Rovo']);
  });

  it('a found subject, a question card, or a fact this build cannot read carries nothing', () => {
    expect(buildCompletion(foldTimeline(journal(answered()), RUN))).to.not.have.property('notFound');
    const asked = foldTimeline(
      journal({ outcome: 'needs_input', answer: 'Which?', partTexts: [], citations: [], confidence: 'Medium', answerMatchType: 'Clarification Needed', notFound: NOT_FOUND }),
      RUN,
    );
    expect(buildCompletion(asked)).to.not.have.property('notFound');
    const saved = (notFound: unknown) =>
      buildAIResponseMessage({ statusCode: 200, data: { answer: ANSWER, citations: [], notFound } as unknown as IAIResponse });
    expect(saved({ subject: 'SAAS launch', line: LINE })).to.not.have.property('notFound');
    expect(saved({ ...NOT_FOUND, hits: -1 })).to.not.have.property('notFound');
  });
});
