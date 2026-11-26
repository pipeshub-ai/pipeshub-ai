/**
 * FU2-4: a model call that fails after streaming part of an answer is retracted on a durable
 * run as today's loop retracts it (main #4442): its message ends, `message_discarded` names it and a `/discarded`
 * answer delta follows, so no partial text stays on screen. A durable run once left the last attempt's text
 * standing: the projector ended it and opened legacy's failed-call message as its replacement instead.
 */
import { expect } from 'chai';
import * as fs from 'fs';
import * as path from 'path';
import { LiveDelta, ProjectedFrame } from '../../../../src/modules/agent-harness/projection/frames';
import { planReplay, ReplayStep } from '../../../../src/modules/agent-harness/projection/replay';
import { RunProjector } from '../../../../src/modules/agent-harness/projection/run-projector';
import { parseStreamId } from '../../../../src/modules/agent-harness/projection/stream-id';
import {
  GoldenJournal,
  Step,
  liveSteps,
  parseSse,
  projectorContext,
  readJournal,
  runSteps,
} from '../../../contracts/support/durable';

const PARTIAL = 'The refund policy is thirty';
const LEGACY_GOLDEN = path.join(__dirname, '../../../contracts/golden/chat_llm_rejected.sse');

/** chat_llm_rejected (the run fails on its model call), with `attempts` calls each streaming the partial text. */
function failedAfterStreaming(attempts: number): { journal: GoldenJournal; steps: Step[] } {
  const golden = readJournal('chat_llm_rejected');
  const failAt = golden.events.findIndex((e) => e.type === 'x.pipeshub.answer.finalized');
  const afterSeq = golden.events[failAt - 1]!.seq;
  const live = [];
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    for (const [n, text] of [PARTIAL.slice(0, 10), PARTIAL.slice(10)].entries()) {
      const frame: LiveDelta = { runId: golden.runId, fence: 1, modelAttempt: attempt, n: n + 1, kind: 'text_delta', data: { text } };
      live.push({ afterSeq, frameId: `d:${golden.runId}:1:${attempt}:${n + 1}`, frame });
    }
  }
  const journal = { ...golden, live };
  return { journal, steps: liveSteps(journal) };
}

const frames = (attempts: number): ProjectedFrame[] => {
  const { journal, steps } = failedAfterStreaming(attempts);
  return runSteps(new RunProjector(projectorContext(journal)), steps);
};

/**
 * What the web app leaves on screen: a discarded message's text is taken away, and so is a message a restart
 * replaces (a resume whose deltas expired restarts the call's message, L-2).
 */
function shown(all: ProjectedFrame[]): string {
  const gone = new Set([
    ...all
      .filter((f) => f.frame.type === 'CUSTOM' && f.frame['name'] === 'message_discarded')
      .map((f) => (f.frame['value'] as { messageId: string }).messageId),
    ...all.filter((f) => typeof f.frame['replacesMessageId'] === 'string').map((f) => String(f.frame['replacesMessageId'])),
  ]);
  return all
    .filter((f) => f.frame.type === 'TEXT_MESSAGE_CONTENT' && !gone.has(String(f.frame['messageId'])))
    .map((f) => String(f.frame['delta']))
    .join('');
}

const discards = (all: ProjectedFrame[]): string[] =>
  all
    .filter((f) => f.frame.type === 'CUSTOM' && f.frame['name'] === 'message_discarded')
    .map((f) => (f.frame['value'] as { messageId: string }).messageId);

describe('FU2-4: a durable model call that fails after partial output is retracted as legacy retracts it', () => {
  it('the streamed text of the failed call is discarded, and the error answer stands', () => {
    const all = frames(1);
    expect(all.filter((f) => f.frame.type === 'TEXT_MESSAGE_CONTENT').map((f) => f.frame['delta']).join('')).to.equal(PARTIAL);
    expect(shown(all)).to.equal('');
    const started = all.filter((f) => f.frame.type === 'TEXT_MESSAGE_START').map((f) => f.frame['messageId']);
    expect(discards(all)).to.deep.equal(started);
    const final = all.find((f) => f.frame.type === 'STATE_SNAPSHOT' && (f.frame['snapshot'] as { final?: boolean }).final);
    expect((final!.frame['snapshot'] as { answer: string }).answer).to.match(/rejected its API key/);
    expect(all.some((f) => 'replacesMessageId' in f.frame)).to.equal(false);
  });

  it('in legacy order: the message ends, message_discarded names it, then the /discarded answer delta', () => {
    const types = frames(1).map((f) => (f.frame.type === 'CUSTOM' ? `CUSTOM:${String(f.frame['name'])}` : f.frame.type));
    const end = types.lastIndexOf('TEXT_MESSAGE_END');
    expect(types.slice(end, end + 3)).to.deep.equal(['TEXT_MESSAGE_END', 'CUSTOM:message_discarded', 'STATE_DELTA']);
    const delta = frames(1)[end + 2]!.frame['delta'] as Array<{ path: string; value: unknown }>;
    expect(delta).to.deep.include({ op: 'replace', path: '/discarded', value: true });
  });

  it('each retried attempt that failed is discarded too (the durable loop retries as legacy does)', () => {
    const all = frames(3);
    const started = all.filter((f) => f.frame.type === 'TEXT_MESSAGE_START').map((f) => f.frame['messageId']);
    expect(started).to.have.length(3);
    expect(discards(all)).to.deep.equal(started);
    expect(shown(all)).to.equal('');
    expect(all.some((f) => 'replacesMessageId' in f.frame)).to.equal(false);
  });

  it('a call that failed before any output is retracted with the frames legacy sends (chat_llm_rejected)', () => {
    const journal = readJournal('chat_llm_rejected');
    const durable = runSteps(new RunProjector(projectorContext(journal)), [{ journal: journal.events }]);
    const kinds = (types: Array<{ type: unknown; name?: unknown }>) =>
      types.map((f) => (f.type === 'CUSTOM' ? `CUSTOM:${String(f.name)}` : String(f.type))).filter((t) => t !== 'CUSTOM:conversation_created');
    const legacy = parseSse(fs.readFileSync(LEGACY_GOLDEN, 'utf8')).map((f) => f.frame as { type: unknown; name?: unknown });
    expect(kinds(durable.map((f) => f.frame))).to.deep.equal(kinds(legacy));
  });

  it('a client that reconnects at any frame keeps no partial text and ends on the error (L-1, L-2)', () => {
    const { journal, steps } = failedAfterStreaming(3);
    const original = runSteps(new RunProjector(projectorContext(journal)), steps);
    expect(new Set(original.map((f) => f.id)).size, 'ids are unique').to.equal(original.length);
    for (const deltas of [journal.live.map((d) => d.frame), []]) {
      for (let cut = 0; cut < original.length; cut += 1) {
        const id = original[cut]!.id;
        const plan = planReplay(journal.runId, journal.events, deltas, parseStreamId(id), {});
        const projector = new RunProjector(projectorContext(journal), plan.resumeAfter);
        projector.restoreLost(plan.lostMessages);
        const rest = projector.begin();
        if (plan.lost !== null) rest.push(...projector.abandon(plan.lost, plan.lost.endSent ? 'already_sent' : { sentAfter: id }));
        rest.push(...runSteps(projector, plan.steps as ReplayStep[] as never, { begin: false }));
        const seen = [...original.slice(0, cut + 1), ...rest];
        const label = `resume after ${id} (${deltas.length} deltas retained)`;
        expect(shown(seen), label).to.equal('');
        expect(seen[seen.length - 1]!.frame.type, label).to.equal('RUN_FINISHED');
      }
    }
  });
});
