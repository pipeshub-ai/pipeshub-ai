/**
 * WP89-N (ADR-RUN-01 D1, WP-89): the settled outcome is the source of the words the chat shows about a run's end.
 * Node advertises `outcome_words`; a settled outcome carrying `presented` reaches the answer (`runOutcome`, on the
 * completion, the final part and the saved message), the answer then being only the model's prose, and a failed
 * run's ending is the same typed outcome. A state notice journaled again after a resume shows again, and a
 * code_task child's time plan shows on its parent's stream.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { JournalEvent, validateJournalAppend } from '../../../src/modules/agent-harness/domain/journal';
import {
  NODE_CAPABILITIES,
  OUTCOME_WORDS_FEATURE,
} from '../../../src/modules/agent-harness/domain/node-capabilities';
import { foldTimeline } from '../../../src/modules/agent-harness/projection/timeline';
import { buildCompletion, buildParts } from '../../../src/modules/agent-harness/projection/transcript';
import { failedOutcome, outcomeView } from '../../../src/modules/agent-harness/projection/outcome-view';
import { ChildInteractions } from '../../../src/modules/agent-harness/projection/child-interactions';
import {
  buildAIFailureResponseMessage,
  buildAIResponseMessage,
} from '../../../src/modules/enterprise_search/utils/utils';
import { withOutcomeClosing } from '../../../src/integrations/slack-bot/src/utils/cut-short';
import { without } from './support/without';

const RUN = 'run-1';
const CLOSING =
  '**report.md isn\'t written yet.** This task stopped because it ran out of working time while it was still gathering sources, before it wrote the file.';
const SETTLED = {
  outcome: {
    kind: 'partial',
    reason: 'time',
    delivered: { sources: 3, notes: ['notes/handoff.json'], kept: true },
    missing: ['report.md'],
    action: 'continue',
  },
  deliverable: { kind: 'file', name: 'report.md' },
  presented: { closing: CLOSING, opening: false },
};

function events(
  specs: Array<[string, Record<string, unknown>, 'node' | 'worker']>,
  runId = RUN,
): JournalEvent[] {
  return specs.map(
    ([type, data, writer], index) =>
      ({
        orgId: 'org-1',
        sessionId: 'ses-1',
        streamId: 'ses-1',
        seq: index + 1,
        runId,
        attemptId: writer === 'worker' ? `${runId}.a1` : undefined,
        fence: writer === 'worker' ? 1 : undefined,
        type,
        schemaVersion: 1,
        critical: !type.startsWith('x.'),
        ts: '2026-11-06T12:00:00.000Z',
        writer,
        visibility: 'owner',
        data,
      }) as JournalEvent,
  );
}

function answered(settled: Record<string, unknown> | null, answer = ''): JournalEvent[] {
  return events([
    ['run.admitted', { runKind: 'interactive', principalRef: 'p_user' }, 'node'],
    ['run.claimed', { attempt: 1, fence: 1, leaseMs: 60000 }, 'node'],
    ...(settled !== null
      ? [['x.pipeshub.outcome.settled', settled, 'worker'] as [string, Record<string, unknown>, 'worker']]
      : []),
    [
      'x.pipeshub.answer.finalized',
      { outcome: 'answered', answer, partTexts: [answer], citations: [], truncatedBy: 'time' },
      'worker',
    ],
    ['run.completed', { outcome: 'answered', truncatedBy: 'time' }, 'node'],
  ]);
}

describe('WP89-N (ADR-RUN-01 D1): the chat words the settled outcome', () => {
  it('WP89-N1: Node advertises outcome_words, and the worker may journal a settled outcome with its framing', () => {
    expect(NODE_CAPABILITIES.features).to.include(OUTCOME_WORDS_FEATURE);
    const append = (data: Record<string, unknown>) => () =>
      validateJournalAppend({
        orgId: 'org-1',
        sessionId: 'ses-1',
        streamId: 'ses-1',
        runId: RUN,
        writer: 'worker',
        events: [{ type: 'x.pipeshub.outcome.settled', schemaVersion: 1, critical: false, visibility: 'owner', data }],
      } as never);
    expect(append(SETTLED)).to.not.throw();
    expect(append({ ...SETTLED, presented: { closing: CLOSING } })).to.throw();
  });

  it('WP89-N2: the outcome rides the completion and its final part, and an empty prose is still an answer', () => {
    const timeline = foldTimeline(answered(SETTLED), RUN);
    const completion = buildCompletion(timeline) as Record<string, unknown>;
    const view = outcomeView(SETTLED)!;
    expect(view).to.deep.include({ kind: 'partial', reason: 'time', action: 'continue', missing: 'report.md', sources: 3, kept: true });
    expect(completion['answer']).to.equal('');
    expect(completion['runOutcome']).to.deep.equal(view);
    const final = buildParts(timeline).find((part) => part['isFinal'] === true);
    expect(final?.['runOutcome']).to.deep.equal(view);
    const message = buildAIResponseMessage({ statusCode: 200, data: completion } as never);
    expect(message.content).to.equal('');
    expect(message.runOutcome).to.deep.equal(view);
  });

  it('WP89-N3: an outcome journaled without its framing (an older worker) is not worded again', () => {
    const older = without(SETTLED, 'presented');
    const completion = buildCompletion(foldTimeline(answered(older, 'Its answer, with its words.'), RUN)) as Record<string, unknown>;
    expect(completion).to.not.have.property('runOutcome');
    expect(completion['answer']).to.equal('Its answer, with its words.');
  });

  it('WP89-N4: the Slack bot, which has no locale, appends the English closing and does not add the notice again', () => {
    const shown = withOutcomeClosing({ content: 'The findings so far.', truncatedBy: 'time', runOutcome: { closing: CLOSING } });
    expect(shown.content).to.equal(`The findings so far.\n\n${CLOSING}`);
    expect(shown).to.not.have.property('truncatedBy');
    expect(withOutcomeClosing({ content: 'Done.', truncatedBy: 'time' })).to.deep.equal({ content: 'Done.', truncatedBy: 'time' });
  });

  it('WP89-N5: a failed run\'s ending is the same typed outcome, its servers and stop riding along', () => {
    const outcome = failedOutcome({ code: 'mcp_tool_withdrawn', message: 'Atlassian no longer offers…', servers: ['Atlassian'] });
    expect(outcome).to.deep.equal({
      kind: 'failed', reason: 'mcp_tool_withdrawn', action: 'retry', sources: 0, kept: false, opening: false,
      closing: 'Atlassian no longer offers…', servers: ['Atlassian'],
    });
    const saved = buildAIFailureResponseMessage('Your admin stopped this agent work for now.', undefined, {
      runId: RUN,
      errorCode: 'operator_stopped',
      runOutcome: failedOutcome({ code: 'operator_stopped', message: 'Your admin stopped this agent work for now.', stop: { scope: 'org', reason: 'cost' } }),
    });
    expect(saved.runOutcome?.stop).to.deep.equal({ scope: 'org', reason: 'cost' });
  });

  it('WP89-N6: a retire after a resume shows again (notices keyed by code and occurrence); others show once', () => {
    const timeline = foldTimeline(
      events([
        ['run.admitted', { runKind: 'interactive', principalRef: 'p_user' }, 'node'],
        ['x.pipeshub.run.notice', { code: 'engine_retired' }, 'node'],
        ['x.pipeshub.run.notice', { code: 'engine_continued' }, 'node'],
        ['x.pipeshub.run.notice', { code: 'engine_retired' }, 'node'],
        ['x.pipeshub.run.notice', { code: 'mcp_unavailable' }, 'worker'],
        ['x.pipeshub.run.notice', { code: 'mcp_unavailable' }, 'worker'],
      ]),
      RUN,
    );
    expect(timeline.notices).to.deep.equal([
      { code: 'engine_retired', occurrence: 1 },
      { code: 'engine_continued', occurrence: 1 },
      { code: 'engine_retired', occurrence: 2 },
      { code: 'mcp_unavailable' },
    ]);
  });

  it('WP89-N7: a code_task child\'s time plan is said on its parent\'s stream, under the child\'s run', async () => {
    const child = 'run-child';
    const plan = { code: 'time_plan', writeBy: '2026-11-06T17:20:00.000Z', finishBy: '2026-11-06T17:45:00.000Z' };
    const journal = events(
      [
        ['run.admitted', { runKind: 'child', principalRef: 'p_user' }, 'node'],
        ['x.pipeshub.run.notice', plan, 'worker'],
        ['x.pipeshub.run.notice', { code: 'mcp_unavailable' }, 'worker'],
      ],
      child,
    );
    const relay = new ChildInteractions(async () => journal, RUN);
    relay.track([{ childRunId: child, role: 'code_task', spawnSeq: 3, live: true }]);
    const frames = (await relay.pull(child)).map((f) => f.frame);
    const notices = frames.filter((f) => f['name'] === 'notice');
    expect(notices).to.deep.equal([{ type: 'CUSTOM', name: 'notice', value: plan, runId: child }]);
  });

  it('G48-N3: a move to an alternate model and a wait for a free sandbox read as notices, each once', () => {
    const timeline = foldTimeline(
      events([
        ['run.admitted', { runKind: 'interactive', principalRef: 'p_user' }, 'node'],
        ['run.yielded', { reason: 'sandbox_wait' }, 'worker'],
        ['run.yielded', { reason: 'sandbox_wait' }, 'worker'],
        ['model.fallback', { from: { model: 'gpt-x' }, to: { model: 'claude-x' }, reason: '5xx' }, 'worker'],
      ]),
      RUN,
    );
    expect(timeline.notices).to.deep.equal([
      { code: 'sandbox_queued' },
      { code: 'model_switched', item: 'claude-x' },
    ]);
  });
});
