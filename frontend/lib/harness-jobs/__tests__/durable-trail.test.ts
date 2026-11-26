import { describe, expect, it } from 'vitest';
import { TrailReducer, finishedAs, frameOf, proseBlocks, trailFromFrames, trailFromParts } from '../durable-trail';
import type { RunTrail } from '../durable-trail';
import { cancelledRun, completedRun, runError } from './stream-fixture';
import { frameScenarios, goldenEvents, goldenScenarios, journalFrames, journalParts, partScenarios, plainText, words } from './fixtures';

const titles = (trail: RunTrail) => trail.entries.map((e) => e.title);
const prose = (trail: RunTrail) =>
  trail.segments.flatMap((s) => (s.intro ?? []).flatMap((b) => ('segments' in b ? b.segments.map((x) => ('text' in x ? x.text : '')) : []))).join(' ');

function expectPlain(trail: RunTrail): void {
  for (const e of trail.entries) {
    expect(plainText(e.title), `title "${e.title}"`).toBe(true);
    expect(plainText(e.detail), `detail "${e.detail}"`).toBe(true);
    expect(plainText(e.answer), `answer "${e.answer}"`).toBe(true);
  }
}

describe('durable frames into the trail', () => {
  it.each(goldenScenarios())('golden %s: every row is plain words, and the run ends with one status word', (scenario) => {
    const trail = trailFromFrames(goldenEvents(scenario), words);
    expectPlain(trail);
    expect(['done', 'couldntFinish', 'stopped', 'needsYou']).toContain(trail.status);
  });

  it.each(frameScenarios())('journal %s: every row is plain words, and the run ends', (scenario) => {
    const trail = trailFromFrames(journalFrames(scenario), words);
    expectPlain(trail);
    expect(trail.status).toBe('done');
  });

  it('a quick tool call: the tool is named in words, and the answer is the last segment', () => {
    const trail = trailFromFrames(goldenEvents('agent_quick_tool_call'), words);
    expect(titles(trail)).toEqual(['Used Search docs']);
    expect(trail.entries[0]).toMatchObject({ kind: 'tool', state: 'done', detail: 'Search Docs: "paid leave"' });
    const last = trail.segments[trail.segments.length - 1];
    expect(last.trail).toBeUndefined();
    expect(prose(trail)).toContain('You get 25 days of paid leave a year.');
    expect(trail.status).toBe('done');
  });

  it('reasoning is one quiet row; what the model thought stays folded inside it, never a step title', () => {
    const trail = trailFromFrames(goldenEvents('agent_quick_reasoning'), words);
    const reasoning = trail.entries.find((e) => e.kind === 'reasoning');
    expect(reasoning).toMatchObject({ title: 'Thought it through', detail: 'The user asks about leave. The handbook says 25 days.', state: 'done' });
    expect(titles(trail)).not.toContain('The user asks about leave.');
  });

  it('a question the run asks is a decision row, and a run that ended to wait for the answer Needs you', () => {
    const trail = trailFromFrames(goldenEvents('agent_ask_user_question'), words);
    const ask = trail.entries.find((e) => e.kind === 'decision');
    expect(ask).toMatchObject({ title: 'Which region should the report cover?', state: 'waiting' });
    expect(ask?.answer).toBeUndefined();
    expect(trail.status).toBe('needsYou');
  });

  it('a run a person stopped says Stopped, and a model that is not set up says Couldn\'t finish', () => {
    expect(trailFromFrames(goldenEvents('agent_cancel_mid_stream'), words).status).toBe('stopped');
    expect(trailFromFrames(goldenEvents('chat_llm_unavailable'), words).status).toBe('couldntFinish');
  });

  it('a tool that failed and was retried shows both calls, the first failed', () => {
    const trail = trailFromFrames(goldenEvents('agent_react_tool_error_then_success'), words);
    const tools = trail.entries.filter((e) => e.kind === 'tool');
    expect(tools.length).toBeGreaterThanOrEqual(2);
    expect(tools.map((t) => t.state)).toContain('failed');
    expect(tools[tools.length - 1].state).toBe('done');
  });

  it('a hand-off to a code task: the parent shows one plain row, and the child\'s own frames stay out', () => {
    const trail = trailFromFrames(journalFrames('u3_parent_with_engine_child'), words);
    expect(titles(trail)).toEqual(['Handed the work to a background agent']);
    expect(trail.segments.map((s) => (s.intro ? 'say' : '') + (s.trail ? 'do' : ''))).toEqual(['saydo', 'say']);
    expect(prose(trail)).toContain('Done: the engine wrote the analysis.');
  });

  it('an engine run as the root: commands, files and the ask it raised, with your answer stamped on it', () => {
    const trail = trailFromFrames(journalFrames('u3_engine_run'), words);
    expect(titles(trail)).toEqual(['Ran report.py', 'Created out/q3-report.md', 'Approved by you: jira create issue', 'Used Jira create issue']);
    expect(trail.entries[0].detail).toBe('python report.py');
    expect(trail.entries[2]).toMatchObject({ kind: 'decision', answer: 'Approved by you', state: 'done' });
  });

  it('live, frame by frame: Working, then Needs you while the ask is open, then Done once answered and finished', () => {
    const frames = journalFrames('u3_engine_run');
    const reducer = new TrailReducer(words);
    const seen: string[] = [];
    for (const frame of frames) {
      reducer.apply(frame);
      const status = reducer.view().status;
      if (seen[seen.length - 1] !== status) seen.push(status);
    }
    expect(seen).toEqual(['working', 'needsYou', 'working', 'done']);
  });

  it('takes the stream client\'s own events (`SSEEvent`) as well as bare and recorded frames', () => {
    const events = completedRun();
    const trail = trailFromFrames(events, words);
    expect(titles(trail)).toEqual(['Used Search']);
    expect(prose(trail)).toContain('Let me search.');
    expect(frameOf({ id: 'j:1', frame: { type: 'RUN_STARTED' } })).toEqual({ type: 'RUN_STARTED' });
    expect(frameOf(null)).toBeNull();
    expect(frameOf({ event: 'X', data: { a: 1 } })).toEqual({ a: 1, type: 'X' });
  });

  it('the chat suites\' stopped and failed streams read Stopped and Couldn\'t finish', () => {
    expect(trailFromFrames(cancelledRun(), words).status).toBe('stopped');
    expect(trailFromFrames(runError(), words).status).toBe('couldntFinish');
    expect(trailFromFrames(runError('abort'), words).status).toBe('stopped');
  });

  it('ignores frames it does not know, so a newer server cannot break the trail', () => {
    const trail = trailFromFrames([{ type: 'SOMETHING_NEW', x: 1 }, { type: 'CUSTOM', name: 'brand_new', value: {} }], words);
    expect(trail.entries).toEqual([]);
    expect(trail.status).toBe('working');
  });

  it('reads the final snapshot to tell how a run ended', () => {
    expect(finishedAs('stopped')).toBe('stopped');
    expect(finishedAs('failed')).toBe('couldntFinish');
    expect(finishedAs('waiting_input')).toBe('needsYou');
    expect(finishedAs('completed')).toBe('done');
    expect(finishedAs(null)).toBe('done');
  });
});

describe('saved parts into the same trail', () => {
  it.each(partScenarios())('journal %s: every row is plain words', (scenario) => {
    expectPlain(trailFromParts(journalParts(scenario).parts, words));
  });

  it.each(['u3_parent_with_engine_child', 'u3_engine_run', 'agent_steered_during_tool', 'agent_steered_after_answer'])(
    '%s: a reload shows the rows the live stream showed',
    (scenario) => {
      const live = titles(trailFromFrames(journalFrames(scenario), words));
      const saved = titles(trailFromParts(journalParts(scenario).parts, words));
      // Saved parts list an engine's asks after its steps rather than where it asked; the rows are the same.
      const isAsk = (title: string) => / · waiting for you$|^(Approved|Declined) by you: /.test(title);
      const asks = (list: string[]) => list.filter(isAsk).sort();
      const work = (list: string[]) => list.filter((title) => !isAsk(title));
      expect(work(saved)).toEqual(work(live));
      expect(asks(saved)).toEqual(asks(live));
    },
  );

  it('a code task\'s child folds into the hand-off row, its goal underneath', () => {
    const saved = trailFromParts(journalParts('u3_parent_with_engine_child').parts, words);
    expect(saved.entries).toHaveLength(1);
    expect(saved.entries[0]).toMatchObject({ title: 'Handed the work to a background agent', detail: 'Analyse in/q3.csv' });
  });

  it('a pending approval in the saved parts Needs you; a decided one carries the answer', () => {
    const pending = trailFromParts([{ type: 'approval', toolName: 'slack__send_message', status: 'pending', interactionId: 'ix.approve.abc' }] as never[], words, { status: 'suspended' });
    expect(pending.status).toBe('needsYou');
    expect(pending.entries[0]).toMatchObject({ title: 'Send message in Slack · waiting for you', state: 'waiting' });
    const denied = trailFromParts([{ type: 'approval', toolName: 'slack__send_message', status: 'denied', interactionId: 'ix.approve.abc' }] as never[], words);
    expect(denied.entries[0]).toMatchObject({ title: 'Declined by you: send message in Slack', answer: 'Declined by you' });
    const expired = trailFromParts([{ type: 'approval', toolName: 'x', status: 'expired' }] as never[], words);
    expect(expired.entries[0].answer).toBe("No answer in time, so it didn't go ahead");
  });

  it('turns prose into paragraphs, one per blank-line break', () => {
    expect(proseBlocks('One.\n\nTwo.\n\n')).toEqual([
      { kind: 'paragraph', segments: [{ kind: 'text', text: 'One.' }] },
      { kind: 'paragraph', segments: [{ kind: 'text', text: 'Two.' }] },
    ]);
  });
});
