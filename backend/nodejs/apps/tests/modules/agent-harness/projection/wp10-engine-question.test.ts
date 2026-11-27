/**
 * AP-13 / J-25 (C3.70): an engine's `ask_user` reaches the chat as the loop's question card, once, when the run
 * ends needs_input; the person's reply is the session's next message, as for the loop.
 */
import { expect } from 'chai';
import { JournalEvent } from '../../../../src/modules/agent-harness/domain/journal';
import { RunProjector } from '../../../../src/modules/agent-harness/projection/run-projector';
import { runTranscript } from '../../../../src/modules/agent-harness/projection/transcript';
import { foldTimeline } from '../../../../src/modules/agent-harness/projection/timeline';
import { projectorContext, readJournal } from '../../../contracts/support/durable';

describe('AP-13 / J-25: an engine question is the chat’s question card (C3.70)', () => {
  const base = readJournal('agent_quick_tool_call');
  const head = base.events.filter(
    (e) => e.type === 'run.admitted' || e.type === 'run.claimed' || e.type === 'run.spec_pinned',
  );
  const at = head[head.length - 1]!;
  const worker = (seq: number, type: string, data: Record<string, unknown>): JournalEvent => ({
    ...at,
    seq,
    type,
    writer: 'worker',
    critical: false,
    attemptId: 'run-golden.a1',
    fence: 1,
    data,
  });
  const toolData = {
    name: 'ask_user_question',
    userIntent: '',
    questions: [
      {
        uuid: 'ask.0123',
        question: 'Which region should the report cover?',
        options: [
          { id: 'opt_eu', label: 'EU', isUserInput: false },
          { id: 'opt_us', label: 'US', isUserInput: false },
        ],
        multiSelect: false,
      },
    ],
  };
  const asked = (seq: number): JournalEvent =>
    worker(seq, 'x.pipeshub.question.asked', { callId: 'ask.0123', status: 'success', toolData });
  // The turn was re-driven after a lost attempt, so the engine asked the same question twice.
  const events: JournalEvent[] = [
    ...head,
    asked(at.seq + 1),
    asked(at.seq + 2),
    worker(at.seq + 3, 'x.pipeshub.answer.finalized', {
      outcome: 'needs_input',
      answer: 'I need the region first.',
      partTexts: ['I need the region first.'],
      citations: [],
      answerMatchType: 'Clarification Needed',
    }),
    { ...at, seq: at.seq + 4, type: 'run.completed', writer: 'node', data: { outcome: 'needs_input' } },
  ];

  it('AP-13: one card, at the run’s end, before its final answer', () => {
    expect(foldTimeline(events, base.runId).questions).to.have.length(1);
    const projector = new RunProjector(projectorContext(base));
    projector.begin();
    const frames = projector.project(events).map((f) => f.frame);
    const cards = frames.filter((f) => f.type === 'CUSTOM' && f['name'] === 'ask_user_question');
    expect(cards).to.have.length(1);
    expect((cards[0]!['value'] as Record<string, unknown>)['toolData']).to.deep.equal(toolData);
    const final = frames.findIndex(
      (f) => f.type === 'STATE_SNAPSHOT' && (f['snapshot'] as Record<string, unknown>)['final'] === true,
    );
    expect(final).to.be.greaterThan(frames.indexOf(cards[0]!));
  });

  it('J-25: the saved turn keeps the card, so the question is still answerable when the person comes back', () => {
    expect(runTranscript(events, base.runId)).to.deep.include({ kind: 'answer', questions: [{ toolData }] });
  });
});
